from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPOSITORY_ROOT / "docs" / "INSTALLATION.md"


class CrossPlatformDocumentationTests(unittest.TestCase):
    def test_guide_covers_install_paths_keyring_launchers_and_safe_smoke(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")

        for required in (
            "Windows",
            "macOS",
            "Linux",
            "python -m pip install",
            "canvas-ed-mcp doctor --show-paths",
            "python -m keyring set",
            "keyring:canvas-ed-mcp/default/canvas",
            "env:CANVAS_API_TOKEN",
            "canvas-ed-mcp serve --profile default",
            "course-sync --help",
            "NOT_RUN",
        ):
            self.assertIn(required, text)

        self.assertNotIn("your_Canvas_API_Token", text)
        self.assertNotIn("Course Material Sync.command", text)


if __name__ == "__main__":
    unittest.main()
