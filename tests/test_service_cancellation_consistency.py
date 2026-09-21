from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock

from photos_indexer.service import CancellationToken, ScanRequest, apply_manifest, scan
from photos_indexer.workflows import WorkflowResult


class ServiceCancellationConsistencyTests(TestCase):
    def test_scan_cancellation_with_non_workflow_runner_result_fails_closed(self) -> None:
        token = CancellationToken()
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> object:
            del root, kwargs
            token.cancel()
            return None

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            cancellation=token,
            scan_runner=runner,  # type: ignore[arg-type]
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertEqual(events[-1]["type"], "completed")
        self.assertEqual(events[-1]["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_apply_cancellation_race_preserves_a_rollback_next_action(self) -> None:
        token = CancellationToken()
        path = Path("runs/run/manifest.json")

        def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
            del manifest_path, kwargs
            token.cancel()
            return WorkflowResult(exit_code=0, manifest_path=path, next_action="rollback_available")

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
            result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.error_codes, ("CANCELLED",))
        self.assertEqual(result.next_action, "rollback_available")

    def test_partial_apply_cancellation_preserves_existing_error_and_recovery_action(self) -> None:
        token = CancellationToken()
        path = Path("runs/run/manifest.json")

        def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
            del manifest_path, kwargs
            token.cancel()
            return WorkflowResult(
                exit_code=1,
                manifest_path=path,
                warning_codes=("PHOTOS_ACCESS_LIMITED",),
                error_codes=("WRITE_FAILED",),
                next_action="retry_failed_operation",
            )

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
            result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.error_codes, ("CANCELLED", "WRITE_FAILED"))
        self.assertEqual(result.warning_codes, ("PHOTOS_ACCESS_LIMITED",))
        self.assertEqual(result.next_action, "retry_failed_operation")

    def test_fatal_apply_cancellation_preserves_existing_error_and_cancelled_code(self) -> None:
        token = CancellationToken()
        path = Path("runs/run/manifest.json")

        def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
            del manifest_path, kwargs
            token.cancel()
            return WorkflowResult(
                exit_code=2,
                manifest_path=path,
                error_codes=("OLLAMA_PREFLIGHT_FAILED",),
                next_action="retry_preflight",
            )

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
            result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("CANCELLED", "OLLAMA_PREFLIGHT_FAILED"))
        self.assertEqual(result.next_action, "retry_preflight")

    def test_cancellation_with_malformed_error_codes_fails_closed(self) -> None:
        token = CancellationToken()
        path = Path("runs/run/manifest.json")

        def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
            del manifest_path, kwargs
            token.cancel()
            return WorkflowResult(exit_code=1, manifest_path=path, error_codes=(["raw"],))  # type: ignore[arg-type]

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
            result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(result.next_action, "fix_fatal_error")

    def test_cancellation_with_malformed_terminal_fields_fails_closed(self) -> None:
        path = Path("runs/run/manifest.json")
        cases = (
            {"exit_code": True},
            {"warning_codes": None},
            {"safe_instruction": "ollama pull /private/raw"},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                token = CancellationToken()

                def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
                    del manifest_path, kwargs
                    token.cancel()
                    values: dict[str, object] = {
                        "exit_code": 1,
                        "manifest_path": path,
                        "error_codes": ("WRITE_UNCERTAIN",),
                    }
                    values.update(updates)
                    return WorkflowResult(**values)  # type: ignore[arg-type]

                with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
                    result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
                self.assertEqual(result.next_action, "fix_fatal_error")

    def test_scan_cancellation_preserves_a_valid_pull_instruction(self) -> None:
        token = CancellationToken()
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            del root, kwargs
            token.cancel()
            return WorkflowResult(
                exit_code=1,
                error_codes=("OLLAMA_MODEL_MISSING",),
                safe_instruction="ollama pull qwen3-vl:8b",
                next_action="retry_preflight",
            )

        result = scan(ScanRequest(runs_root=Path("runs")), events.append, cancellation=token, scan_runner=runner)

        self.assertEqual(result.error_codes, ("CANCELLED", "OLLAMA_MODEL_MISSING"))
        self.assertEqual(events[-1]["safe_instruction"], "ollama pull qwen3-vl:8b")

    def test_scan_converts_a_success_race_after_cancellation_to_cancelled(self) -> None:
        token = CancellationToken()
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            del root, kwargs
            token.cancel()
            return WorkflowResult(exit_code=0, manifest_path=Path("runs/run/manifest.json"))

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            cancellation=token,
            scan_runner=runner,
        )

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.error_codes, ("CANCELLED",))
        self.assertEqual(result.next_action, "none")
        self.assertEqual(events[-1]["exit_code"], 1)
        self.assertEqual(events[-1]["error_codes"], ["CANCELLED"])

    def test_apply_converts_a_success_race_after_cancellation_to_cancelled(self) -> None:
        token = CancellationToken()
        path = Path("/safe/runs/run/manifest.json")

        def runner(manifest_path: Path, **kwargs: object) -> WorkflowResult:
            del manifest_path, kwargs
            token.cancel()
            return WorkflowResult(exit_code=0, manifest_path=path)

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=path):
            result = apply_manifest(path, lambda event: None, cancellation=token, apply_runner=runner)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.error_codes, ("CANCELLED",))
        self.assertEqual(result.next_action, "none")
