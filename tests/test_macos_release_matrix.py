from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))


class MacOSReleaseMatrixTests(unittest.TestCase):
    def test_reviewed_uv_version_matches_owner_device(self) -> None:
        from ci import macos_release_matrix as matrix

        self.assertEqual("uv 0.11.28", matrix.REVIEWED_UV_VERSION)

    def test_source_version_contract_avoids_legacy_module(self) -> None:
        from ci import macos_release_matrix as matrix

        def fake_query(argv: tuple[str, ...], **_kwargs: object) -> str:
            if argv[:3] == ("command", "-v", "uv"):
                return "/usr/bin/uv"
            if argv[-1] == "--version":
                return "uv 0.11.28"
            if argv[:2] == ("git", "status"):
                return ""
            if argv[:2] == ("git", "rev-parse"):
                return "0123456789abcdef"
            if argv[:2] == ("git", "branch"):
                return "hy/phase-3-4-portability"
            if argv[:2] == ("git", "log"):
                return "1"
            raise AssertionError(f"unexpected query: {argv!r}")

        actual_run = matrix.MatrixRunner.run
        actual_scrub = matrix.scrub_environment
        version_results: list[bool] = []

        def stop_after_version(runner: object, name: str, argv: object, **kwargs: object) -> bool:
            if name == "Python 3.10 version contract":
                version_results.append(actual_run(runner, name, argv, **kwargs))
                return False
            return True

        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(
                python=("3.10",),
                offline=False,
                skip_keychain=True,
                report_dir=Path(temporary),
            )
            with (
                patch.object(matrix.sys, "platform", "darwin"),
                patch.object(matrix, "PRIVATE_SENTINELS", ()),
                patch.object(matrix, "_query", side_effect=fake_query),
                patch.object(matrix, "_venv_python", return_value=Path(sys.executable)),
                patch.object(
                    matrix,
                    "scrub_environment",
                    side_effect=lambda environment: {
                        **actual_scrub(environment),
                        "PYTHONPATH": str(SOURCE_ROOT),
                    },
                ),
                patch.object(matrix.MatrixRunner, "run", stop_after_version),
            ):
                matrix.run_matrix(args)

        self.assertEqual([True], version_results)

    def test_plan_is_available_without_macos_or_provider_credentials(self) -> None:
        import subprocess
        import sys

        environment = dict(os.environ)
        environment["CANVAS_API_TOKEN"] = "must-not-appear"
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(REPOSITORY_ROOT / "ci" / "macos_release_matrix.py"),
                "--plan",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        combined = result.stdout + result.stderr
        self.assertIn("Python 3.10 locked source matrix", combined)
        self.assertIn("Python 3.14 exact wheel install", combined)
        self.assertIn("Native macOS Keychain canary", combined)
        self.assertIn("MCP host UI: NOT_RUN", combined)
        self.assertNotIn("must-not-appear", combined)
        self.assertNotIn("sync_courses.py", combined)

    def test_environment_scrub_removes_provider_credentials_only(self) -> None:
        from ci.macos_release_matrix import scrub_environment

        cleaned = scrub_environment(
            {
                "PATH": "/usr/bin",
                "HOME": "/example-home",
                "CANVAS_API_TOKEN": "secret",
                "ED_API_TOKEN": "secret",
                "EDSTEM_TOKEN": "secret",
                "GRADESCOPE_PASSWORD": "secret",
                "CI_CANVAS_NEVER_READ": "secret",
                "PYTHON_KEYRING_BACKEND": "keyrings.alt.file.PlaintextKeyring",
                "KEYCHAIN_PATH": "/tmp/not-the-default-keychain",
                "UV_CACHE_DIR": "/tmp/uv-cache",
            }
        )

        self.assertEqual("/usr/bin", cleaned["PATH"])
        self.assertEqual("/example-home", cleaned["HOME"])
        self.assertEqual("/tmp/uv-cache", cleaned["UV_CACHE_DIR"])
        self.assertFalse(
            any(
                name in cleaned
                for name in (
                    "CANVAS_API_TOKEN",
                    "ED_API_TOKEN",
                    "EDSTEM_TOKEN",
                    "GRADESCOPE_PASSWORD",
                    "CI_CANVAS_NEVER_READ",
                    "PYTHON_KEYRING_BACKEND",
                    "KEYCHAIN_PATH",
                )
            )
        )

    def test_report_has_fixed_manual_boundaries_and_no_command_output(self) -> None:
        from ci.macos_release_matrix import MatrixResult, StepResult, write_report

        result = MatrixResult(
            commit="0123456789abcdef",
            macos_version="15.0",
            architecture="arm64",
            steps=(StepResult("Synthetic suite", "PASS", 0, 1.25),),
            automated_result="PASS",
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.md"
            write_report(report, result)
            text = report.read_text(encoding="utf-8")

        self.assertIn("| Synthetic suite | PASS | 0 |", text)
        self.assertIn("Native MCP host UI | NOT_RUN", text)
        self.assertIn("Optional Canvas/Ed real-account catalog | NOT_RUN", text)
        self.assertIn("Private legacy actual sync | NOT_RUN", text)
        self.assertNotIn("stdout", text.casefold())
        self.assertNotIn("stderr", text.casefold())

    def test_keychain_canary_always_attempts_cleanup_without_printing_value(self) -> None:
        from ci.macos_keychain_canary import run_canary

        class FakeBackend:
            priority = 1

            def __init__(self) -> None:
                self.value: str | None = None
                self.deleted = False

            def set_password(self, service: str, account: str, value: str) -> None:
                self.value = value

            def get_password(self, service: str, account: str) -> str | None:
                return self.value

            def delete_password(self, service: str, account: str) -> None:
                self.deleted = True
                self.value = None

        backend = FakeBackend()
        outcome = run_canary(backend=backend, canary="synthetic-secret-canary")

        self.assertTrue(outcome.round_trip)
        self.assertTrue(outcome.cleanup_confirmed)
        self.assertTrue(backend.deleted)
        self.assertNotIn("synthetic-secret-canary", json.dumps(outcome.as_dict()))

    def test_keychain_canary_cleans_up_after_round_trip_mismatch(self) -> None:
        from ci.macos_keychain_canary import run_canary

        class MismatchBackend:
            priority = 1

            def __init__(self) -> None:
                self.deleted = False

            def set_password(self, service: str, account: str, value: str) -> None:
                pass

            def get_password(self, service: str, account: str) -> str | None:
                return None

            def delete_password(self, service: str, account: str) -> None:
                self.deleted = True

        backend = MismatchBackend()
        outcome = run_canary(backend=backend, canary="synthetic-secret-canary")

        self.assertFalse(outcome.round_trip)
        self.assertTrue(outcome.cleanup_confirmed)
        self.assertTrue(backend.deleted)

    def test_keychain_canary_fails_when_cleanup_cannot_be_confirmed(self) -> None:
        from ci.macos_keychain_canary import run_canary

        class CleanupBrokenBackend:
            priority = 1

            def __init__(self) -> None:
                self.value: str | None = None

            def set_password(self, service: str, account: str, value: str) -> None:
                self.value = value

            def get_password(self, service: str, account: str) -> str | None:
                return self.value

            def delete_password(self, service: str, account: str) -> None:
                raise RuntimeError("synthetic cleanup failure")

        outcome = run_canary(
            backend=CleanupBrokenBackend(), canary="synthetic-secret-canary"
        )

        self.assertTrue(outcome.round_trip)
        self.assertFalse(outcome.cleanup_confirmed)
        self.assertEqual("cleanup_failed", outcome.error_category)

    def test_keychain_canary_deletes_after_backend_stores_then_raises(self) -> None:
        from ci.macos_keychain_canary import run_canary

        class PartialStoreBackend:
            priority = 1

            def __init__(self) -> None:
                self.value: str | None = None
                self.deleted = False

            def set_password(self, service: str, account: str, value: str) -> None:
                self.value = value
                raise RuntimeError("synthetic partial store")

            def get_password(self, service: str, account: str) -> str | None:
                return self.value

            def delete_password(self, service: str, account: str) -> None:
                self.deleted = True
                self.value = None

        backend = PartialStoreBackend()
        outcome = run_canary(backend=backend, canary="synthetic-secret-canary")

        self.assertTrue(backend.deleted)
        self.assertTrue(outcome.cleanup_confirmed)
        self.assertFalse(outcome.round_trip)

    def test_python_selectors_are_restricted_to_supported_versions(self) -> None:
        from ci.macos_release_matrix import normalize_python_versions

        self.assertEqual(("3.10", "3.14"), normalize_python_versions(None))
        self.assertEqual(("3.12",), normalize_python_versions(("3.12", "3.12")))
        for unsafe in ("3.9", "3.15", "../../3.14", "3.14/../../escape", "3.14\n"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                normalize_python_versions((unsafe,))

    def test_offline_build_is_owned_by_uv(self) -> None:
        source = (REPOSITORY_ROOT / "ci" / "macos_release_matrix.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('runner.environment["UV_OFFLINE"] = "1"', source)
        self.assertIn('(uv, "build", "--out-dir", dist)', source)
        self.assertNotIn('"-m", "build"', source)

    def test_keychain_canary_rejects_unsafe_backend_before_write(self) -> None:
        from ci.macos_keychain_canary import run_canary

        class NullBackend:
            priority = 0

            def set_password(self, service: str, account: str, value: str) -> None:
                raise AssertionError("unsafe backend must not be written")

        outcome = run_canary(backend=NullBackend())

        self.assertFalse(outcome.backend_available)
        self.assertEqual("unsafe_backend", outcome.backend_category)
        self.assertFalse(outcome.round_trip)
        self.assertFalse(outcome.cleanup_confirmed)

    def test_production_keychain_gate_rejects_positive_priority_non_macos_backend(self) -> None:
        from ci.macos_keychain_canary import _backend_status

        class EncryptedFileBackend:
            priority = 1

        available, category = _backend_status(
            EncryptedFileBackend(), injected_for_test=False
        )

        self.assertFalse(available)
        self.assertEqual("not_macos_keychain", category)


if __name__ == "__main__":
    unittest.main()
