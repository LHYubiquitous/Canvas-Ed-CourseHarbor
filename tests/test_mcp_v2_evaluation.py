from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MCPV2EvaluationTests(unittest.TestCase):
    def test_v1_pin_stays_until_documented_migration_gates_pass(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        evaluation = (ROOT / "docs" / "MCP_V2_EVALUATION.md").read_text(encoding="utf-8")

        self.assertIn('"mcp>=1.29.0,<2.0.0"', pyproject)
        for required in (
            "v2.2.0",
            "MCPServer",
            "mcp.server.fastmcp",
            "2026-07-28",
            "Migration decision: DEFER",
            "INTEGRATION_VERIFIED",
            "official migration guide",
        ):
            self.assertIn(required, evaluation)


if __name__ == "__main__":
    unittest.main()
