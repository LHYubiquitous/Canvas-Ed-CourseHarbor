from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GUIDE = REPOSITORY_ROOT / "docs" / "AUTHENTICATION.md"


class AuthenticationDocumentationTests(unittest.TestCase):
    def test_guide_distinguishes_personal_canvas_ed_and_oauth(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")

        for required in (
            "Manual Canvas token",
            "OAuth 2.0",
            "Approved Integrations",
            "DELETE /login/oauth2/token",
            "https://edstem.org/settings/api-tokens",
            "beta",
            "keyring:canvas-ed-mcp/default/canvas",
            "keyring:canvas-ed-mcp/default/ed",
            "401",
            "403",
            "DEGRADED",
        ):
            self.assertIn(required, text)

        self.assertNotIn("access_token=", text)
        self.assertNotIn("Bearer ey", text)


if __name__ == "__main__":
    unittest.main()
