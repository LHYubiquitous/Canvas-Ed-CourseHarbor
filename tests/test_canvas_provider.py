from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class CanvasProviderTests(unittest.TestCase):
    def run_package_code(self, code: str):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            return subprocess.run(
                [sys.executable, "-B", "-c", textwrap.dedent(code)],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_list_courses_follows_same_instance_pagination_and_uses_stable_identity(self) -> None:
        result = self.run_package_code(
            """
            import json
            import httpx
            from canvas_ed_mcp.providers.canvas import CanvasProvider

            requests = []
            def respond(request):
                requests.append(request)
                assert request.headers["authorization"] == "Bearer synthetic-token"
                if request.url.params.get("page") == "2":
                    return httpx.Response(200, json=[{
                        "id": 202,
                        "name": "Algorithms Later",
                        "course_code": "TEST2000",
                        "term": {"name": "2027 Semester 1"},
                    }])
                assert request.url.path == "/api/v1/courses"
                assert request.url.params.get("enrollment_state[]") == "active"
                assert request.url.params.get("include[]") == "term"
                return httpx.Response(
                    200,
                    json=[{
                        "id": 101,
                        "name": "Algorithms",
                        "course_code": "TEST2000",
                        "term": {"name": "2026 Semester 2"},
                    }],
                    headers={"Link": '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next"'},
                )

            client = httpx.Client(transport=httpx.MockTransport(respond))
            provider = CanvasProvider(
                "https://canvas.example.edu/api/v1", "synthetic-token", client=client
            )
            try:
                courses = provider.list_courses()
            finally:
                provider.close()
            print(json.dumps({
                "keys": [course.key for course in courses],
                "codes": [course.code for course in courses],
                "terms": [course.term for course in courses],
                "requests": len(requests),
            }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(
            ["canvas:canvas.example.edu:101", "canvas:canvas.example.edu:202"],
            data["keys"],
        )
        self.assertEqual(["TEST2000", "TEST2000"], data["codes"])
        self.assertEqual(["2026 Semester 2", "2027 Semester 1"], data["terms"])
        self.assertEqual(2, data["requests"])

    def test_cross_origin_pagination_is_rejected_before_token_can_leak(self) -> None:
        result = self.run_package_code(
            """
            import httpx
            from canvas_ed_mcp.providers.base import ProviderError
            from canvas_ed_mcp.providers.canvas import CanvasProvider

            requests = []
            def respond(request):
                requests.append(str(request.url))
                return httpx.Response(
                    200,
                    json=[],
                    headers={"Link": '<https://attacker.example/courses?page=2>; rel="next"'},
                )

            provider = CanvasProvider(
                "https://canvas.example.edu/api/v1",
                "synthetic-token",
                client=httpx.Client(transport=httpx.MockTransport(respond)),
            )
            try:
                try:
                    provider.list_courses()
                except ProviderError as error:
                    print(error.category)
                else:
                    raise AssertionError("cross-origin pagination was accepted")
            finally:
                provider.close()
            assert len(requests) == 1
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("unsafe_pagination", result.stdout.strip())

    def test_instance_identity_preserves_only_non_default_https_port(self) -> None:
        result = self.run_package_code(
            """
            import json
            import httpx
            from canvas_ed_mcp.providers.canvas import CanvasProvider
            from canvas_ed_mcp.sync.model import RemoteKind, SourceKey

            client = httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(500)
            ))
            default = CanvasProvider(
                "https://canvas.example.edu:443/api/v1", "synthetic-token", client=client
            )
            custom = CanvasProvider(
                "https://canvas.example.edu:8443/api/v1", "synthetic-token", client=client
            )
            key = SourceKey(
                provider="canvas",
                instance=custom.instance,
                course_id="synthetic-course",
                kind=RemoteKind.FILE,
                remote_id="synthetic-file",
            )
            print(json.dumps({
                "default": default.instance,
                "custom": custom.instance,
                "key_instance": key.instance,
            }))
            client.close()
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "default": "canvas.example.edu",
                "custom": "canvas.example.edu:8443",
                "key_instance": "canvas.example.edu:8443",
            },
            json.loads(result.stdout),
        )


if __name__ == "__main__":
    unittest.main()
