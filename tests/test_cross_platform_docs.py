from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPOSITORY_ROOT / "docs" / "INSTALLATION.md"
README = REPOSITORY_ROOT / "README.md"
CHINESE_README = REPOSITORY_ROOT / "README.zh-CN.md"


class CrossPlatformDocumentationTests(unittest.TestCase):
    def test_readme_is_ordered_for_install_setup_and_daily_use(self) -> None:
        text = README.read_text(encoding="utf-8")
        headings = (
            "## 1. Install",
            "## 2. Store credentials",
            "## 3. Create your profile",
            "## 4. Daily use",
            "## 5. Command-line and MCP use",
        )
        positions = [text.index(heading) for heading in headings]
        self.assertEqual(sorted(positions), positions)

        for required in (
            "canvas_ed_mcp-0.2.0rc16-py3-none-any.whl",
            "python -m keyring set canvas-ed-mcp/default canvas",
            "canvas-ed-mcp setup --profile default --dry-run",
            "course_material_sync",
            "this_week_due",
            "this_semester_due",
            "canvas-ed-mcp serve --profile default",
            "https://github.com/LHYubiquitous/canvas-ed-mcp-public/releases",
            "https://edstem.org/au/settings/api-tokens",
            "Account → Settings",
        ):
            self.assertIn(required, text)

        self.assertNotIn("private `v0.2.0rc4`", text)
        self.assertNotIn("github.com/LHYubiquitous/canvas-ed-mcp/issues", text)

    def test_chinese_readme_covers_first_run_daily_use_and_troubleshooting(self) -> None:
        text = CHINESE_README.read_text(encoding="utf-8")

        for required in (
            "## 1. 安装",
            "## 2. 获取并保存 API token",
            "Account → Settings",
            "https://edstem.org/au/settings/api-tokens",
            "canvas-ed-mcp setup --profile default --dry-run",
            "course_material_sync",
            "this_week_due",
            "this_semester_due",
            "canvas-ed-mcp serve --profile default",
            "## 7. 常见问题",
        ):
            self.assertIn(required, text)

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
