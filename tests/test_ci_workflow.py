from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


class CIWorkflowTests(unittest.TestCase):
    def test_workflow_covers_three_os_and_supported_python_range(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(runner, text)
        for version in ("3.10", "3.11", "3.12", "3.13", "3.14", "3.15"):
            self.assertIn(f'"{version}"', text)
        self.assertIn(
            "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6",
            text,
        )
        self.assertIn(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7",
            text,
        )
        self.assertIn("persist-credentials: false", text)

    def test_workflow_builds_tests_installed_cli_and_audits_artifacts(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("python -m build", text)
        self.assertIn("unittest discover -s tests", text)
        self.assertIn("ci/verify_distribution.py", text)
        self.assertIn("ci/verify_release_metadata.py", text)
        self.assertIn("ci/smoke_installed.py", text)
        self.assertIn("ci/release_gate.py", text)
        self.assertIn("Install the audited clean-source candidate", text)
        self.assertIn("canvas-ed-mcp-0.1.0rc1-source.tar.gz", text)
        self.assertIn("uv sync --locked --all-extras --no-dev", text)
        self.assertIn('"--force-reinstall", str(wheel)', text)
        self.assertLess(
            text.index("Install the exact built wheel"),
            text.index("Install the audited clean-source candidate"),
        )
        self.assertIn("Smoke-test exact wheel MCP protocol", text)
        self.assertIn("Smoke-test clean-source MCP protocol", text)
        self.assertIn("uv lock --check", text)
        self.assertIn("canvas-ed-mcp", text)
        self.assertIn("course-sync", text)
        self.assertIn("ci/macos_release_matrix.py --plan", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("${{ secrets.", text)
        self.assertNotIn("requirements.txt", text)


if __name__ == "__main__":
    unittest.main()
