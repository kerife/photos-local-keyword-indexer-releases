from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest import mock

from photos_indexer.ipc import IPCProtocolError, _safe_service_event, serve


class IPCTerminalConsistencyTests(unittest.TestCase):
    def test_ipc_cancelled_preflight_boolean_exit_code_is_not_accepted_as_cancellation(self) -> None:
        """Preflight cancellation must not coerce a malformed boolean code."""
        incoming = io.StringIO(
            '{"id":"preflight-bool","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
            '{"id":"cancel-preflight-bool","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def preflight_handler(payload, token):
            del payload
            while not token.is_cancelled():
                pass
            return False, {"next_action": "none"}  # type: ignore[return-value]

        serve(incoming, outgoing, preflight_handler=preflight_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "preflight-bool", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            messages,
        )

    def test_ipc_cancelled_failed_preflight_preserves_failure_and_cancelled_codes(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-failed","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
            '{"id":"cancel-preflight-failed","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def preflight_handler(payload, token):
            del payload
            while not token.is_cancelled():
                pass
            return 2, {"error_codes": ["OLLAMA_UNAVAILABLE"], "next_action": "retry_preflight"}

        serve(incoming, outgoing, preflight_handler=preflight_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-failed",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["CANCELLED", "OLLAMA_UNAVAILABLE"],
                "next_action": "retry_preflight",
            },
            messages,
        )

    def test_ipc_cancelled_emitted_boolean_exit_code_is_not_accepted_as_cancellation(self) -> None:
        """Cancellation normalization must not coerce JSON booleans to exit codes."""
        incoming = io.StringIO(
            '{"id":"apply-bool","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-bool","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path
            while not token.is_cancelled():
                pass
            emit({"type": "completed", "exit_code": False, "next_action": "none"})
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=1, error_codes=("CANCELLED",), next_action="none")

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-bool", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn(
            {"id": "apply-bool", "event": "completed", "exit_code": 1, "error_codes": ["CANCELLED"], "next_action": "none"},
            messages,
        )

    def test_ipc_cancellation_race_preserves_mutation_recovery_action(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=0, next_action="rollback_available")

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "error_codes": ["CANCELLED"],
                "next_action": "rollback_available",
            },
            messages,
        )

    def test_ipc_emitted_completion_preserves_warnings_and_recovery_action_on_cancel(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path
            while not token.is_cancelled():
                pass
            emit(
                {
                    "type": "completed",
                    "exit_code": 0,
                    "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                    "next_action": "rollback_available",
                }
            )
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=0, next_action="rollback_available")

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                "error_codes": ["CANCELLED"],
                "next_action": "rollback_available",
            },
            messages,
        )

    def test_ipc_returned_completion_preserves_warnings_and_recovery_action_on_cancel(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=0,
                warning_codes=("PHOTOS_ACCESS_LIMITED",),
                next_action="rollback_available",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                "error_codes": ["CANCELLED"],
                "next_action": "rollback_available",
            },
            messages,
        )

    def test_ipc_malformed_unhashable_exit_code_fails_closed_as_unsafe_result(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-malformed-exit","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit, token
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=[], next_action="none")  # type: ignore[arg-type]

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-malformed-exit", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("WORKER_OPERATION_FAILED", outgoing.getvalue())

    def test_ipc_returned_malformed_code_collections_fail_closed_before_projection(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-malformed-codes","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit, token
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=1,
                error_codes="FAILED",  # type: ignore[arg-type]
                warning_codes="LIMITED",  # type: ignore[arg-type]
                next_action="retry_failed_operation",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-malformed-codes", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("WORKER_OPERATION_FAILED", outgoing.getvalue())

    def test_ipc_returned_success_with_malformed_warning_codes_fails_closed(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-malformed-warning","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit, token
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=0,
                warning_codes="LIMITED",  # type: ignore[arg-type]
                next_action="none",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-malformed-warning", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )

    def test_ipc_cancelled_malformed_error_codes_fail_closed_as_unsafe_result(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-malformed-errors","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-malformed-errors","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=1, error_codes=(['raw'],), next_action="manual_review")  # type: ignore[arg-type]

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-malformed-errors", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("WORKER_OPERATION_FAILED", outgoing.getvalue())

    def test_cancelled_handler_result_preserves_error_code_in_terminal_event(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=1, error_codes=("CANCELLED",), next_action="none")

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "error_codes": ["CANCELLED"],
                "next_action": "none",
            },
            messages,
        )

    def test_ipc_partial_cancellation_preserves_existing_error_and_recovery_action(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=1,
                warning_codes=("PHOTOS_ACCESS_LIMITED",),
                error_codes=("WRITE_FAILED",),
                next_action="retry_failed_operation",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                "error_codes": ["CANCELLED", "WRITE_FAILED"],
                "next_action": "retry_failed_operation",
            },
            messages,
        )

    def test_ipc_fatal_cancellation_preserves_existing_error_and_cancelled_code(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-fatal","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-fatal","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=2,
                error_codes=("OLLAMA_PREFLIGHT_FAILED",),
                next_action="retry_preflight",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-fatal",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["CANCELLED", "OLLAMA_PREFLIGHT_FAILED"],
                "next_action": "retry_preflight",
            },
            messages,
        )

    def test_ipc_rejects_success_with_errors_during_cancellation(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-unsafe-success","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-unsafe-success","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path, emit
            while not token.is_cancelled():
                pass
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(
                exit_code=0,
                error_codes=("WRITE_FAILED",),
                next_action="rollback_available",
            )

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "apply-unsafe-success", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn(
            {"id": "apply-unsafe-success", "event": "completed", "exit_code": 1, "error_codes": ["CANCELLED"], "next_action": "rollback_available"},
            messages,
        )

    def test_ipc_emitted_partial_cancellation_preserves_existing_error_and_recovery_action(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()

        def apply_handler(path, emit, token):
            del path
            while not token.is_cancelled():
                pass
            emit(
                {
                    "type": "completed",
                    "exit_code": 1,
                    "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                    "error_codes": ["WRITE_FAILED"],
                    "next_action": "retry_failed_operation",
                }
            )
            from photos_indexer.workflows import WorkflowResult
            return WorkflowResult(exit_code=1, error_codes=("WRITE_FAILED",), next_action="retry_failed_operation")

        with mock.patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "apply-1",
                "event": "completed",
                "exit_code": 1,
                "warning_codes": ["PHOTOS_ACCESS_LIMITED"],
                "error_codes": ["CANCELLED", "WRITE_FAILED"],
                "next_action": "retry_failed_operation",
            },
            messages,
        )

    def test_successful_completion_cannot_carry_error_codes(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({
                "type": "completed",
                "exit_code": 0,
                "error_codes": ["ANALYSIS_FAILED"],
                "next_action": "none",
            })

    def test_successful_completion_cannot_carry_a_pull_instruction(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({
                "type": "completed",
                "exit_code": 0,
                "safe_instruction": "ollama pull qwen3-vl:8b",
                "next_action": "none",
            })

    def test_successful_preflight_cannot_carry_error_codes(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-inconsistent","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        serve(
            incoming,
            outgoing,
            preflight_handler=lambda payload, token: (
                0,
                {"error_codes": ["OLLAMA_UNAVAILABLE"], "next_action": "retry_preflight"},
            ),
        )

        self.assertIn(
            {"id": "preflight-inconsistent", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_successful_preflight_cannot_carry_a_repair_instruction(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-instruction","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        serve(
            incoming,
            outgoing,
            preflight_handler=lambda payload, token: (
                0,
                {"safe_instruction": "ollama pull qwen3-vl:4b", "next_action": "none"},
            ),
        )

        self.assertIn(
            {"id": "preflight-instruction", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_failed_preflight_requires_stable_error_codes(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-missing-error","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        serve(
            incoming,
            outgoing,
            preflight_handler=lambda payload, token: (2, {"next_action": "retry_preflight"}),
        )

        self.assertIn(
            {"id": "preflight-missing-error", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_successful_preflight_cannot_advertise_recovery_action(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-action","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        serve(
            incoming,
            outgoing,
            preflight_handler=lambda payload, token: (0, {"next_action": "fix_fatal_error"}),
        )

        self.assertIn(
            {"id": "preflight-action", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_preflight_completion_requires_a_next_action(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-no-action","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        serve(
            incoming,
            outgoing,
            preflight_handler=lambda payload, token: (0, {"models": {"qwen3-vl:4b": "0.12.7"}}),
        )

        self.assertIn(
            {"id": "preflight-no-action", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )
