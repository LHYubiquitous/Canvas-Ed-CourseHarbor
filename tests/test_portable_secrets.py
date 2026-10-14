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


class PortableSecretTests(unittest.TestCase):
    def run_package_code(self, code: str, *, extra_env: dict[str, str] | None = None):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        environment.update(extra_env or {})
        with tempfile.TemporaryDirectory() as temporary:
            return subprocess.run(
                [sys.executable, "-B", "-c", textwrap.dedent(code)],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_env_and_fake_keyring_references_resolve_without_exposing_values(self) -> None:
        result = self.run_package_code(
            """
            import json
            from canvas_ed_mcp.secrets import resolve_secret

            class FakeKeyring:
                priority = 1
                def get_password(self, service, account):
                    assert (service, account) == ("canvas-ed-mcp/default", "canvas")
                    return "keyring-secret-canary"

            env_value = resolve_secret("env:SYNTHETIC_CANVAS_TOKEN", environ={"SYNTHETIC_CANVAS_TOKEN": "synthetic-canary"})
            keyring_value = resolve_secret("keyring:canvas-ed-mcp/default/canvas", backend=FakeKeyring())
            print(json.dumps({"env_length": len(env_value), "keyring_length": len(keyring_value)}))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"env_length": 16, "keyring_length": 21}, json.loads(result.stdout))
        self.assertNotIn("secret-canary", result.stdout + result.stderr)

    def test_missing_secret_and_unsafe_keyring_fail_closed(self) -> None:
        result = self.run_package_code(
            """
            import json
            from canvas_ed_mcp.secrets import SecretUnavailable, keyring_status, resolve_secret

            class NullKeyring:
                priority = 0
                def get_password(self, service, account):
                    raise AssertionError("unsafe backend must not be queried")

            failures = []
            for reference, kwargs in (
                ("env:MISSING_TOKEN", {"environ": {}}),
                ("keyring:canvas-ed-mcp/default/canvas", {"backend": NullKeyring()}),
            ):
                try:
                    resolve_secret(reference, **kwargs)
                except SecretUnavailable as error:
                    failures.append(error.category)
            status = keyring_status(backend=NullKeyring())
            print(json.dumps({"failures": failures, "status": status.category, "available": status.available}))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(["missing_env", "unsafe_backend"], data["failures"])
        self.assertEqual("unsafe_backend", data["status"])
        self.assertFalse(data["available"])

    def test_redaction_removes_bearer_and_signed_query_values(self) -> None:
        result = self.run_package_code(
            """
            from canvas_ed_mcp.secrets import redact_text
            value = redact_text(
                "Authorization: Bearer bearer-secret-canary "
                "https://files.example.test/x?token=query-secret-canary&signature=signed-secret-canary"
                "&X-Amz-Signature=aws-secret-canary#fragment-secret-canary"
            )
            print(value)
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        combined = result.stdout + result.stderr
        self.assertNotIn("secret-canary", combined)
        self.assertIn("Bearer [REDACTED]", combined)
        self.assertIn("token=[REDACTED]", combined)
        self.assertIn("signature=[REDACTED]", combined)
        self.assertIn("X-Amz-Signature=[REDACTED]", combined)
        self.assertIn("#[REDACTED]", combined)

    def test_doctor_never_prints_token_environment_values(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        environment["CANVAS_API_TOKEN"] = "synthetic-canary"
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "canvas_ed_mcp.cli", "doctor"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("synthetic-canary", result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertIn("keyring", data)
        self.assertNotIn("CANVAS_API_TOKEN", data)


if __name__ == "__main__":
    unittest.main()
