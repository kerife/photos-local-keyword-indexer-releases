from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

from photos_indexer.adapters import PhotoScriptPermissionError, PhotosAccessError
from photos_indexer.ipc import (
    IPCProtocolError,
    IPCRequest,
    _WorkerServer,
    _safe_preflight_details,
    _safe_service_event,
    parse_request_line,
    serve,
)
from photos_indexer.service import ScanRequest, scan
from photos_indexer.workflows import WorkflowResult


class IPCTests(unittest.TestCase):
    def test_scan_payload_accepts_strict_random_selection(self) -> None:
        request = parse_request_line(
            '{"id":"scan-random","command":"scan","payload":{"limit":1,"runs_root":"/runs","random_selection":true}}\n'
        )
        self.assertTrue(request.payload["random_selection"])

    def test_scan_payload_rejects_non_boolean_random_selection(self) -> None:
        with self.assertRaises(IPCProtocolError):
            parse_request_line(
                '{"id":"scan-random","command":"scan","payload":{"limit":1,"runs_root":"/runs","random_selection":1}}\n'
            )

    def test_tcc_denial_reaches_jsonl_with_actionable_surface_without_raw_error(self) -> None:
        """The native app must distinguish Photos and Automation TCC failures."""
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"

        for denied_error, expected_code, expected_action in (
            (PhotosAccessError(), "PHOTOS_ACCESS_DENIED", "grant_photos_access"),
            (PhotoScriptPermissionError(), "PHOTOS_AUTOMATION_DENIED", "grant_photos_automation"),
        ):
            with self.subTest(expected_code=expected_code):
                incoming = io.StringIO(
                    '{"id":"scan-tcc","command":"scan","payload":{"limit":1,"runs_root":"'
                    + runs_root
                    + '"}}\n'
                )
                outgoing = io.StringIO()

                def scan_handler(payload, emit, token):
                    del payload

                    def denied_runner(root: Path, **kwargs: object) -> WorkflowResult:
                        del root, kwargs
                        raise denied_error

                    return scan(
                        ScanRequest(runs_root=Path("runs"), limit=1),
                        emit,
                        cancellation=token,
                        scan_runner=denied_runner,
                    )

                with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
                    serve(incoming, outgoing, scan_handler=scan_handler)

                messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
                self.assertEqual(
                    [message for message in messages if message["id"] == "scan-tcc"],
                    [
                        {"id": "scan-tcc", "event": "started", "operation": "scan"},
                        {
                            "id": "scan-tcc",
                            "event": "completed",
                            "exit_code": 2,
                            "warning_codes": [],
                            "error_codes": [expected_code],
                            "next_action": expected_action,
                        },
                    ],
                )
                self.assertNotIn("PhotoScript", outgoing.getvalue())
                self.assertNotIn("-1743", outgoing.getvalue())

    def test_worker_maps_an_adapter_tcc_exception_that_escapes_the_service(self) -> None:
        """A future adapter path must not collapse a TCC denial to a generic error."""
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-escaped-tcc","command":"scan","payload":{"limit":1,"runs_root":"'
            + runs_root
            + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, emit, token
            raise PhotosAccessError()

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(
            [message for message in messages if message["id"] == "scan-escaped-tcc"],
            [
                {
                    "id": "scan-escaped-tcc",
                    "event": "completed",
                    "exit_code": 2,
                    "warning_codes": [],
                    "error_codes": ["PHOTOS_ACCESS_DENIED"],
                    "next_action": "grant_photos_access",
                },
            ],
        )
        self.assertNotIn("WORKER_OPERATION_FAILED", outgoing.getvalue())

    def test_eof_cancels_an_active_worker_before_server_shutdown(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-eof","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()
        handler_started = Event()
        release_handler = Event()
        cancellation_states: list[bool] = []
        finished = Event()

        def scan_handler(payload, emit, token):
            handler_started.set()
            while not token.is_cancelled() and not release_handler.wait(0.01):
                pass
            cancellation_states.append(token.is_cancelled())
            # Simulate a boundary race: the worker finishes its current unit
            # after EOF set the token, but returns its stale success result.
            return WorkflowResult(exit_code=0, next_action="none")

        def run_server() -> None:
            try:
                with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
                    serve(incoming, outgoing, scan_handler=scan_handler)
            finally:
                finished.set()

        thread = Thread(target=run_server, daemon=True)
        thread.start()
        self.assertTrue(handler_started.wait(0.5))
        try:
            self.assertTrue(finished.wait(0.5), "serve did not cooperatively cancel its worker at EOF")
        finally:
            release_handler.set()
            thread.join(1)

        self.assertEqual(cancellation_states, [True])
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "scan-eof",
                "event": "completed",
                "exit_code": 1,
                "error_codes": ["CANCELLED"],
                "next_action": "none",
            },
            messages,
        )
        self.assertNotIn(
            {"id": "scan-eof", "event": "completed", "exit_code": 0, "next_action": "none"},
            messages,
        )

    def test_ipc_review_cancellation_does_not_report_success(self) -> None:
        """A review finishing after cancel must have a cancelled terminal state."""
        outgoing = io.StringIO()
        handler_started = Event()
        release_review = Event()

        def incoming_lines():
            yield '{"id":"review-1","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{}}}\n'
            self.assertTrue(handler_started.wait(1))
            yield '{"id":"cancel-1","command":"cancel","payload":{}}\n'
            # The generator is resumed only after serve has handled cancel,
            # making the race deterministic before releasing review.
            release_review.set()

        def review_handler(path, selections):
            del selections
            handler_started.set()
            self.assertTrue(release_review.wait(1))
            return Path(path)

        with patch(
            "photos_indexer.ipc.validate_app_manifest_path",
            return_value=Path("/runs/source/manifest.json"),
        ):
            serve(incoming_lines(), outgoing, review_handler=review_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        review_completions = [
            message for message in messages
            if message.get("id") == "review-1" and message.get("event") == "completed"
        ]
        self.assertEqual(len(review_completions), 1)
        self.assertEqual(review_completions[0]["exit_code"], 1)
        self.assertEqual(review_completions[0]["error_codes"], ["CANCELLED"])
        self.assertNotEqual(review_completions[0]["exit_code"], 0)

    def test_writer_failure_does_not_block_server_shutdown_at_eof(self) -> None:
        for failure_stage in ("write", "flush"):
            with self.subTest(failure_stage=failure_stage):
                class FailingOutput:
                    def write(self, text: str) -> int:
                        if failure_stage == "write":
                            raise BrokenPipeError("reader closed")
                        return len(text)

                    def flush(self) -> None:
                        if failure_stage == "flush":
                            raise BrokenPipeError("reader closed")

                finished = Event()

                def run_server() -> None:
                    try:
                        serve(io.StringIO("not-json\nstill-not-json\n"), FailingOutput())
                    finally:
                        finished.set()

                thread = Thread(target=run_server, daemon=True)
                thread.start()

                self.assertTrue(finished.wait(0.5), "serve blocked while joining a failed output queue")

    def test_retry_after_terminal_preflight_error_is_not_rejected_as_busy(self) -> None:
        class BlockingOutput:
            def __init__(self) -> None:
                self._buffer = io.StringIO()
                self.first_error_written = Event()
                self.release_first_error = Event()

            def write(self, text: str) -> int:
                written = self._buffer.write(text)
                if '"id":"preflight-fail","event":"error","code":"WORKER_OPERATION_FAILED"' in text:
                    self.first_error_written.set()
                    self.release_first_error.wait(1)
                return written

            def flush(self) -> None:
                return None

            def getvalue(self) -> str:
                return self._buffer.getvalue()

        output = BlockingOutput()
        attempts = 0
        second_attempt_called = Event()

        def preflight_handler(payload, token):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("boom")
            second_attempt_called.set()
            return 0, {"models": {"qwen3-vl:4b": "0.12.7"}, "next_action": "none"}

        server = _WorkerServer(
            output,
            scan_handler=None,
            apply_handler=None,
            rollback_handler=None,
            review_handler=None,
            preflight_handler=preflight_handler,
        )

        server.start(parse_request_line('{"id":"preflight-fail","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}'))
        self.assertTrue(output.first_error_written.wait(1))

        server.start(parse_request_line('{"id":"preflight-retry","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}'))
        output.release_first_error.set()
        self.assertTrue(second_attempt_called.wait(1))
        server.join()

        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn(
            {"id": "preflight-fail", "event": "error", "code": "WORKER_OPERATION_FAILED"},
            messages,
        )
        self.assertIn(
            {"id": "preflight-retry", "event": "started", "operation": "preflight"},
            messages,
        )
        self.assertIn(
            {"id": "preflight-retry", "event": "completed", "exit_code": 0, "models": {"qwen3-vl:4b": "0.12.7"}, "next_action": "none"},
            messages,
        )
        self.assertNotIn(
            {"id": "preflight-retry", "event": "error", "code": "BUSY"},
            messages,
        )

    def test_parser_requires_an_explicit_runs_root_for_scan_requests(self) -> None:
        with self.assertRaises(IPCProtocolError):
            parse_request_line(
                '{"id":"scan-missing-root","command":"scan","payload":{"limit":1,"model_policy":"single","apple_maps":false}}'
            )

    def test_parser_accepts_strict_scan_request(self) -> None:
        root = "/Users/example/Library/Application Support/Photos Local Keyword Indexer/runs"
        request = parse_request_line('{"id":"2","command":"scan","payload":{"limit":1,"model_policy":"single","apple_maps":false,"runs_root":"' + root + '"}}')

        self.assertEqual(request, IPCRequest("2", "scan", {"limit": 1, "model_policy": "single", "apple_maps": False, "runs_root": root}))

    def test_server_rejects_an_unsafe_runs_root_before_invoking_scan_handler(self) -> None:
        incoming = io.StringIO(
            '{"id":"scan-1","command":"scan","payload":{"runs_root":"relative-runs"}}\n'
        )
        outgoing = io.StringIO()
        handler_called = False

        def scan_handler(payload, emit, token):
            nonlocal handler_called
            handler_called = True
            return WorkflowResult(exit_code=0)

        serve(incoming, outgoing, scan_handler=scan_handler)

        self.assertFalse(handler_called)
        self.assertEqual(
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
            [{"id": "scan-1", "event": "error", "code": "RUNS_ROOT_INVALID"}],
        )

    def test_server_rejects_manifest_outside_private_application_support(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-unsafe","command":"apply","payload":{"manifest":"/tmp/manifest.json"}}\n'
        )
        outgoing = io.StringIO()
        handler_called = False

        def apply_handler(path, emit, token):
            nonlocal handler_called
            handler_called = True
            return WorkflowResult(exit_code=0)

        serve(incoming, outgoing, apply_handler=apply_handler)

        self.assertFalse(handler_called)
        self.assertEqual(
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
            [{"id": "apply-unsafe", "event": "error", "code": "MANIFEST_PATH_INVALID"}],
        )

    def test_review_rejects_an_unsafe_output_manifest_path(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-unsafe","command":"review","payload":{"manifest":"/tmp/manifest.json","selections":{}}}\n'
        )
        outgoing = io.StringIO()

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=[Path("/safe/source/manifest.json"), ValueError]):
            serve(incoming, outgoing, review_handler=lambda path, selections: Path("/tmp/reviewed/manifest.json"))

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn({"id": "review-unsafe", "event": "error", "code": "UNSAFE_REVIEW_RESULT"}, messages)

    def test_review_rejects_output_manifest_with_unpaired_unicode_surrogate(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-unicode","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{}}}\n'
        )
        outgoing = io.StringIO()
        unsafe_path = Path("/runs/reviewed/\ud800/manifest.json")

        with patch(
            "photos_indexer.ipc.validate_app_manifest_path",
            side_effect=[Path("/runs/source/manifest.json"), unsafe_path],
        ):
            serve(
                incoming,
                outgoing,
                review_handler=lambda path, selections: unsafe_path,
            )

        self.assertEqual(
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
            [
                {"id": "review-unicode", "event": "started", "operation": "review"},
                {"id": "review-unicode", "event": "error", "code": "UNSAFE_REVIEW_RESULT"},
            ],
        )
        self.assertNotIn("manifest", outgoing.getvalue())

    def test_server_rejects_manifest_outside_app_storage_before_mutation(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-unsafe","command":"apply","payload":{"manifest":"/tmp/manifest.json"}}\n'
        )
        outgoing = io.StringIO()
        handler_called = False

        def apply_handler(path, emit, token):
            nonlocal handler_called
            handler_called = True
            return WorkflowResult(exit_code=0)

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=ValueError):
            serve(incoming, outgoing, apply_handler=apply_handler)

        self.assertFalse(handler_called)
        self.assertEqual(
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
            [{"id": "apply-unsafe", "event": "error", "code": "MANIFEST_PATH_INVALID"}],
        )

    def test_review_rejects_an_unsafe_handler_manifest_path_without_forwarding_it(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-path","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{}}}\n'
        )
        outgoing = io.StringIO()

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=[Path("/runs/source/manifest.json"), ValueError]):
            serve(
                incoming,
                outgoing,
                review_handler=lambda path, selections: Path("/tmp/.exports-secret/manifest.json"),
            )

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn({"id": "review-path", "event": "error", "code": "UNSAFE_REVIEW_RESULT"}, messages)
        self.assertNotIn(".exports-secret", outgoing.getvalue())

    def test_parser_rejects_unknown_command_and_extra_fields(self) -> None:
        with self.assertRaises(IPCProtocolError):
            parse_request_line('{"id":"2","command":"delete","payload":{}}')
        with self.assertRaises(IPCProtocolError):
            parse_request_line('{"id":"2","command":"scan","payload":{},"raw":"forbidden"}')

    def test_parser_validates_review_payload(self) -> None:
        request = parse_request_line(
            '{"id":"review-1","command":"review","payload":{"manifest":"/safe/manifest.json","selections":{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":["playa"]}}}'
        )
        self.assertEqual(request.command, "review")
        with self.assertRaises(IPCProtocolError):
            parse_request_line(
                '{"id":"review-2","command":"review","payload":{"manifest":"/safe/manifest.json","selections":{"not-a-uuid":["playa"]}}}'
            )

    def test_parser_validates_strict_caption_selections(self) -> None:
        request = parse_request_line(
            '{"id":"review-caption","command":"review","payload":{"manifest":"/safe/manifest.json","selections":{},"caption_selections":{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":true}}}'
        )
        self.assertEqual(
            request.payload["caption_selections"],
            {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": True},
        )
        for value in ("true", 1, None):
            with self.assertRaises(IPCProtocolError):
                parse_request_line(
                    '{"id":"review-caption","command":"review","payload":{"manifest":"/safe/manifest.json","selections":{},"caption_selections":{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":'
                    + json.dumps(value)
                    + "}}}"
                )

    def test_server_serializes_sanitized_events_and_rejects_invalid_json(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            'not-json\n'
            '{"id":"1","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            self.assertEqual(payload["limit"], 1)
            emit({"type": "photo_progress", "uuid": "aaaaaaaa", "state": "ready", "keywords_count": 1})
            return WorkflowResult(exit_code=0)

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertEqual(messages[0]["event"], "error")
        self.assertEqual(messages[0]["code"], "INVALID_REQUEST")
        self.assertIn({"id": "1", "event": "photo_progress", "uuid": "aaaaaaaa", "state": "ready", "keywords_count": 1}, messages)
        self.assertIn({"id": "1", "event": "completed", "exit_code": 0, "next_action": "none"}, messages)
        self.assertNotIn("private", outgoing.getvalue().lower())

    def test_server_rejects_unsafe_synthetic_workflow_completion(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-unsafe-next","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            return WorkflowResult(exit_code=0, next_action="bad\naction")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertIn(
            {"id": "scan-unsafe-next", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("bad\\naction", outgoing.getvalue())

    def test_server_preserves_valid_pull_instruction_in_emitted_completion(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-emitted-pull","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, token
            emit({
                "type": "completed",
                "exit_code": 2,
                "error_codes": ["OLLAMA_MODEL_MISSING"],
                "safe_instruction": "ollama pull qwen3-vl:8b",
                "next_action": "retry_preflight",
            })
            return WorkflowResult(exit_code=2, error_codes=("OLLAMA_MODEL_MISSING",), next_action="retry_preflight")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        self.assertIn(
            {
                "id": "scan-emitted-pull",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["OLLAMA_MODEL_MISSING"],
                "safe_instruction": "ollama pull qwen3-vl:8b",
                "next_action": "retry_preflight",
            },
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_server_preserves_valid_pull_instruction_in_fallback_completion(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-fallback-pull","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, emit, token
            return WorkflowResult(
                exit_code=2,
                error_codes=("OLLAMA_MODEL_MISSING",),
                safe_instruction="ollama pull qwen3-vl:8b",
                next_action="retry_preflight",
            )

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        self.assertIn(
            {
                "id": "scan-fallback-pull",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["OLLAMA_MODEL_MISSING"],
                "safe_instruction": "ollama pull qwen3-vl:8b",
                "next_action": "retry_preflight",
            },
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_server_rejects_invalid_pull_instruction_in_fallback_completion(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-unsafe-pull","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, emit, token
            return WorkflowResult(
                exit_code=2,
                error_codes=("OLLAMA_MODEL_MISSING",),
                safe_instruction="ollama pull qwen3-vl:8b && open https://example.invalid",
                next_action="retry_preflight",
            )

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "scan-unsafe-pull", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("example.invalid", outgoing.getvalue())

    def test_server_reports_unsafe_completion_emitted_by_workflow_handler(self) -> None:
        """An invalid handler completion must not disappear without a terminal event."""
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-unsafe-handler","command":"scan","payload":{"limit":1,"runs_root":"'
            + runs_root
            + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, token
            emit({"type": "completed", "exit_code": 0, "next_action": "bad\\naction"})
            return WorkflowResult(exit_code=0, next_action="none")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "scan-unsafe-handler", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )

    def test_server_rejects_terminal_manifest_outside_requested_runs_root(self) -> None:
        """A handler cannot redirect the native client to an unrelated manifest."""
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-manifest-drift","command":"scan","payload":{"limit":1,"runs_root":"'
            + runs_root
            + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, token
            emit({
                "type": "completed",
                "exit_code": 0,
                "manifest": "/tmp/manifest.json",
                "next_action": "review_then_apply",
            })
            return WorkflowResult(exit_code=0, next_action="none")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "scan-manifest-drift", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )
        self.assertNotIn("/tmp/manifest.json", outgoing.getvalue())

    def test_server_rejects_completion_without_exit_code_before_reaching_swift(self) -> None:
        """Every completed event must satisfy the native decoder contract."""
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-missing-exit","command":"scan","payload":{"limit":1,"runs_root":"'
            + runs_root
            + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            del payload, token
            emit({"type": "completed", "next_action": "none"})
            return WorkflowResult(exit_code=0, next_action="none")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "scan-missing-exit", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"},
            messages,
        )

    def test_preflight_projection_rejects_untrusted_ollama_details(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-1","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        def unsafe_preflight(payload, token):
            return 0, {
                "models": {"qwen3-vl:4b": "0.12.7\\nprivate-path"},
                "next_action": "none",
            }

        serve(incoming, outgoing, preflight_handler=unsafe_preflight)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertIn(
            {"id": "preflight-1", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            messages,
        )
        self.assertNotIn("private-path", outgoing.getvalue())

    def test_preflight_projection_rejects_path_like_ollama_version(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_preflight_details({
                "models": {"qwen3-vl:4b": "0.32.1/private-path"},
                "next_action": "none",
            })

    def test_preflight_projection_rejects_an_unbounded_ollama_version(self) -> None:
        version = "0." + ("1" * 100) + ".0"

        with self.assertRaises(IPCProtocolError):
            _safe_preflight_details({"models": {"qwen3-vl:4b": version}})

    def test_preflight_projection_rejects_unsafe_manual_install_instruction(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-2","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        def unsafe_preflight(payload, token):
            return 2, {
                "error_codes": ["OLLAMA_MODEL_MISSING"],
                "safe_instruction": "ollama pull qwen3-vl:4b && open https://example.invalid",
                "next_action": "fix_fatal_error",
            }

        serve(incoming, outgoing, preflight_handler=unsafe_preflight)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertIn(
            {"id": "preflight-2", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            messages,
        )
        self.assertNotIn("example.invalid", outgoing.getvalue())

    def test_default_preflight_reports_loopback_offline_without_transport_details(self) -> None:
        from photos_indexer.adapters import OllamaEndpointUnavailableError

        class OfflineVision:
            def check_model(self, model: str) -> str:
                raise OllamaEndpointUnavailableError("private socket and traceback")

        incoming = io.StringIO(
            '{"id":"preflight-offline","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()
        with patch("photos_indexer.ipc.OllamaVisionClient", return_value=OfflineVision()):
            serve(incoming, outgoing)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-offline",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["OLLAMA_UNAVAILABLE"],
                "next_action": "retry_preflight",
            },
            messages,
        )
        self.assertNotIn("private socket", outgoing.getvalue())
        self.assertNotIn("traceback", outgoing.getvalue())

    def test_default_preflight_checks_a_duplicate_adaptive_model_only_once(self) -> None:
        checked: list[str] = []

        class Vision:
            def check_model(self, model: str) -> str:
                checked.append(model)
                return "0.32.1"

        incoming = io.StringIO(
            '{"id":"preflight-duplicate","command":"preflight",'
            '"payload":{"models":["qwen3-vl:4b","qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()
        with (
            patch("photos_indexer.ipc.OllamaVisionClient", return_value=Vision()),
            patch("photos_indexer.ipc._photoscript_preflight"),
        ):
            serve(incoming, outgoing)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-duplicate",
                "event": "completed",
                "exit_code": 0,
                "models": {"qwen3-vl:4b": "0.32.1"},
                "next_action": "none",
            },
            messages,
        )
        self.assertEqual(checked, ["qwen3-vl:4b"])

    def test_default_preflight_does_not_emit_a_pull_hint_for_an_invalid_inventory(self) -> None:
        from photos_indexer.adapters import AdapterError

        class InvalidInventoryVision:
            def check_model(self, model: str) -> str:
                del model
                raise AdapterError("malformed /api/tags payload")

        incoming = io.StringIO(
            '{"id":"preflight-invalid-inventory","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()
        with patch("photos_indexer.ipc.OllamaVisionClient", return_value=InvalidInventoryVision()):
            serve(incoming, outgoing)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-invalid-inventory",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["OLLAMA_PREFLIGHT_FAILED"],
                "next_action": "retry_preflight",
            },
            messages,
        )
        self.assertNotIn("safe_instruction", outgoing.getvalue())
        self.assertNotIn("malformed", outgoing.getvalue())

    def test_default_preflight_rejects_policy_forbidden_cloud_model_without_retry_or_pull(self) -> None:
        from photos_indexer.adapters import OllamaModelPolicyError

        class CloudRejectedVision:
            def check_model(self, model: str) -> str:
                del model
                raise OllamaModelPolicyError("model policy detail")

        incoming = io.StringIO(
            '{"id":"preflight-cloud","command":"preflight","payload":{"models":["vision:cloud"]}}\n'
        )
        outgoing = io.StringIO()
        with patch("photos_indexer.ipc.OllamaVisionClient", return_value=CloudRejectedVision()):
            serve(incoming, outgoing)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-cloud",
                "event": "completed",
                "exit_code": 2,
                "error_codes": ["MODEL_INVALID"],
                "next_action": "fix_fatal_error",
            },
            messages,
        )
        self.assertNotIn("safe_instruction", outgoing.getvalue())
        self.assertNotIn("model policy detail", outgoing.getvalue())

    def test_default_preflight_reports_photoscript_compatibility_before_scan(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError

        class Vision:
            def check_model(self, model: str) -> str:
                del model
                return "0.32.1"

        incoming = io.StringIO(
            '{"id":"preflight-script","command":"preflight","payload":{"models":["vision:local"]}}\n'
        )
        outgoing = io.StringIO()
        with patch("photos_indexer.ipc.OllamaVisionClient", return_value=Vision()), patch(
            "photos_indexer.ipc._photoscript_preflight",
            side_effect=PhotoScriptUnavailableError(),
        ):
            serve(incoming, outgoing)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "preflight-script",
                "event": "completed",
                "exit_code": 2,
                "models": {"vision:local": "0.32.1"},
                "error_codes": ["PHOTOSCRIPT_UNAVAILABLE"],
                "next_action": "fix_fatal_error",
            },
            messages,
        )
        self.assertNotIn("-2741", outgoing.getvalue())

    def test_scan_maps_photoscript_compile_error_to_actionable_event_without_raw_text(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError

        incoming = io.StringIO(
            '{"id":"scan-script","command":"scan","payload":{"runs_root":"/runs","limit":1}}\n'
        )
        outgoing = io.StringIO()

        def broken_scan(payload, emit, token):
            del payload, emit, token
            raise PhotoScriptUnavailableError()

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/safe/runs")):
            serve(incoming, outgoing, scan_handler=broken_scan)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {
                "id": "scan-script",
                "event": "completed",
                "exit_code": 2,
                "warning_codes": [],
                "error_codes": ["PHOTOSCRIPT_UNAVAILABLE"],
                "next_action": "fix_fatal_error",
            },
            messages,
        )
        self.assertNotIn("-2741", outgoing.getvalue())

    def test_preflight_projection_rejects_an_invalid_exit_code(self) -> None:
        incoming = io.StringIO(
            '{"id":"preflight-3","command":"preflight","payload":{"models":["qwen3-vl:4b"]}}\n'
        )
        outgoing = io.StringIO()

        def unsafe_preflight(payload, token):
            return "success", {"next_action": "none"}

        serve(incoming, outgoing, preflight_handler=unsafe_preflight)
        self.assertIn(
            {"id": "preflight-3", "event": "error", "code": "UNSAFE_PREFLIGHT_RESULT"},
            [json.loads(line) for line in outgoing.getvalue().splitlines()],
        )

    def test_service_event_projection_rejects_wrong_types_and_unbounded_photo_identifiers(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "photo_progress", "uuid": 123, "state": "ready", "keywords_count": 1})
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "photo_progress", "uuid": "a" * 65, "state": "ready", "keywords_count": 1})
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "photo_progress", "uuid": "aaaaaaaa", "state": "ready", "keywords_count": -1})
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "photo_progress", "uuid": "", "state": "analysis_failed"})

    def test_service_event_projection_keeps_progress_metadata_bounded_and_caption_free(self) -> None:
        event = _safe_service_event({
            "type": "photo_progress", "uuid": "aaaaaaaa", "state": "ready",
            "model_used": "qwen3-vl:4b", "model_reason": "single_policy", "keywords_count": 2,
        })
        self.assertNotIn("caption", json.dumps(event).casefold())

    def test_service_event_projection_rejects_an_unknown_model_reason(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({
                "type": "photo_progress",
                "uuid": "aaaaaaaa",
                "state": "ready",
                "model_reason": "private_reason",
            })

    def test_service_event_projection_rejects_manifest_path_traversal(self) -> None:
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({
                "type": "completed",
                "exit_code": 0,
                "manifest": "/runs/../outside/manifest.json",
                "next_action": "review_then_apply",
            })
        with self.assertRaises(IPCProtocolError):
            _safe_service_event({
                "type": "completed",
                "exit_code": 0,
                "manifest": "runs/reviewed/manifest.json",
                "next_action": "review_then_apply",
            })

    def test_server_returns_the_new_reviewed_manifest_path(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-1","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{}}}\n'
        )
        outgoing = io.StringIO()

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=[Path("/runs/source/manifest.json"), Path("/runs/reviewed/manifest.json")]):
            serve(incoming, outgoing, review_handler=lambda path, selections: Path("/runs/reviewed/manifest.json"))

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn({"id": "review-1", "event": "started", "operation": "review"}, messages)
        self.assertIn(
            {"id": "review-1", "event": "completed", "exit_code": 0, "manifest": "/runs/reviewed/manifest.json", "next_action": "review_then_apply"},
            messages,
        )

    def test_legacy_two_argument_review_handler_still_accepts_caption_payload(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-caption","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{},"caption_selections":{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":true}}}\n'
        )
        outgoing = io.StringIO()

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=[Path("/runs/source/manifest.json"), Path("/runs/reviewed/manifest.json")]):
            serve(incoming, outgoing, review_handler=lambda path, selections: Path("/runs/reviewed/manifest.json"))

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn(
            {"id": "review-caption", "event": "completed", "exit_code": 0, "manifest": "/runs/reviewed/manifest.json", "next_action": "review_then_apply"},
            messages,
        )

    def test_three_argument_review_handler_receives_caption_selections(self) -> None:
        incoming = io.StringIO(
            '{"id":"review-caption","command":"review","payload":{"manifest":"/runs/source/manifest.json","selections":{},"caption_selections":{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":true}}}\n'
        )
        outgoing = io.StringIO()
        received: list[object] = []

        def review_handler(path: Path, selections: dict[str, list[str]], captions: dict[str, bool]) -> Path:
            received.extend((path, selections, captions))
            return Path("/runs/reviewed/manifest.json")

        with patch("photos_indexer.ipc.validate_app_manifest_path", side_effect=[Path("/runs/source/manifest.json"), Path("/runs/reviewed/manifest.json")]):
            serve(incoming, outgoing, review_handler=review_handler)

        self.assertEqual(
            received,
            [
                Path("/runs/source/manifest.json"),
                {},
                {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": True},
            ],
        )

    def test_cancel_jsonl_acknowledges_and_marks_an_active_mutation(self) -> None:
        incoming = io.StringIO(
            '{"id":"apply-1","command":"apply","payload":{"manifest":"/runs/reviewed/manifest.json"}}\n'
            '{"id":"cancel-1","command":"cancel","payload":{}}\n'
        )
        outgoing = io.StringIO()
        cancelled = []
        handler_started = Event()

        def apply_handler(path: Path, emit, token):
            self.assertEqual(path, Path("/runs/reviewed/manifest.json"))
            handler_started.set()
            while not token.is_cancelled():
                handler_started.wait(0.01)
            cancelled.append(token.is_cancelled())
            return WorkflowResult(exit_code=1, error_codes=("CANCELLED",), next_action="none")

        with patch("photos_indexer.ipc.validate_app_manifest_path", return_value=Path("/runs/reviewed/manifest.json")):
            serve(incoming, outgoing, apply_handler=apply_handler)
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertTrue(cancelled)
        self.assertIn(
            {"id": "cancel-1", "event": "completed", "exit_code": 0, "next_action": "cancellation_requested"},
            messages,
        )
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

    def test_late_cancel_after_completed_event_is_a_noop(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        outgoing = io.StringIO()
        completed_sent = Event()
        allow_finish = Event()
        cancelled_states: list[bool] = []

        def scan_handler(payload, emit, token):
            emit({"type": "completed", "exit_code": 0, "next_action": "none"})
            completed_sent.set()
            allow_finish.wait(0.5)
            cancelled_states.append(token.is_cancelled())
            return WorkflowResult(exit_code=0, next_action="none")

        server = _WorkerServer(
            outgoing,
            scan_handler=scan_handler,
            apply_handler=None,
            rollback_handler=None,
            review_handler=None,
            preflight_handler=None,
        )
        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            server.start(parse_request_line(
                '{"id":"scan-1","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}'
            ))
            self.assertTrue(completed_sent.wait(0.1))
            server.cancel(parse_request_line('{"id":"cancel-1","command":"cancel","payload":{}}'))
            allow_finish.set()
            server.join()
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]

        self.assertEqual(cancelled_states, [False])
        self.assertIn(
            {"id": "cancel-1", "event": "completed", "exit_code": 0, "next_action": "none"},
            messages,
        )

    def test_server_does_not_append_worker_failed_after_handler_already_completed(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-late-error","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            emit({"type": "completed", "exit_code": 0, "next_action": "none"})
            raise RuntimeError("late failure after terminal event")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(
            [message for message in messages if message["id"] == "scan-late-error"],
            [{"id": "scan-late-error", "event": "completed", "exit_code": 0, "next_action": "none"}],
        )

    def test_server_rejects_events_emitted_after_completed(self) -> None:
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        incoming = io.StringIO(
            '{"id":"scan-post-complete","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}\n'
        )
        outgoing = io.StringIO()

        def scan_handler(payload, emit, token):
            emit({"type": "completed", "exit_code": 0, "next_action": "none"})
            emit({"type": "photo_progress", "uuid": "aaaaaaaa", "state": "ready", "keywords_count": 1})
            return WorkflowResult(exit_code=0, next_action="none")

        with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)):
            serve(incoming, outgoing, scan_handler=scan_handler)

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(
            [message for message in messages if message["id"] == "scan-post-complete"],
            [{"id": "scan-post-complete", "event": "completed", "exit_code": 0, "next_action": "none"}],
        )

    def test_busy_request_is_rejected_before_validating_its_payload(self) -> None:
        outgoing = io.StringIO()
        runs_root = "/safe/Application Support/Photos Local Keyword Indexer/runs"
        started = Event()
        allow_finish = Event()

        def scan_handler(payload, emit, token):
            started.set()
            allow_finish.wait(0.5)
            return WorkflowResult(exit_code=0, next_action="none")

        server = _WorkerServer(
            outgoing,
            scan_handler=scan_handler,
            apply_handler=None,
            rollback_handler=None,
            review_handler=None,
            preflight_handler=None,
        )

        with (
            patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path(runs_root)),
            patch("photos_indexer.ipc.validate_app_manifest_path") as validate_manifest,
        ):
            server.start(parse_request_line(
                '{"id":"scan-1","command":"scan","payload":{"limit":1,"runs_root":"' + runs_root + '"}}'
            ))
            self.assertTrue(started.wait(0.1))
            server.start(parse_request_line(
                '{"id":"apply-busy","command":"apply","payload":{"manifest":"/tmp/manifest.json"}}'
            ))
            allow_finish.set()
            server.join()

        validate_manifest.assert_not_called()
        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn({"id": "apply-busy", "event": "error", "code": "BUSY"}, messages)

    def test_completed_event_does_not_release_single_operation_gate_before_handler_returns(self) -> None:
        """A terminal event cannot let a second Photos mutation overlap its handler."""
        outgoing = io.StringIO()
        manifest = Path("/safe/runs/reviewed/manifest.json")
        first_completed = Event()
        allow_first_return = Event()
        second_started = Event()

        def apply_handler(path, emit, token):
            del path, token
            emit({"type": "completed", "exit_code": 0, "next_action": "rollback_available"})
            first_completed.set()
            allow_first_return.wait(0.5)
            return WorkflowResult(exit_code=0, next_action="rollback_available")

        def rollback_handler(path, emit, token):
            del path, emit, token
            second_started.set()
            return WorkflowResult(exit_code=0, next_action="none")

        server = _WorkerServer(
            outgoing,
            scan_handler=None,
            apply_handler=apply_handler,
            rollback_handler=rollback_handler,
            review_handler=None,
            preflight_handler=None,
        )
        with patch("photos_indexer.ipc.validate_app_manifest_path", return_value=manifest):
            server.start(parse_request_line(
                '{"id":"apply-1","command":"apply","payload":{"manifest":"/safe/runs/reviewed/manifest.json"}}'
            ))
            self.assertTrue(first_completed.wait(0.5))
            server.start(parse_request_line(
                '{"id":"rollback-1","command":"rollback","payload":{"manifest":"/safe/runs/reviewed/manifest.json"}}'
            ))
            self.assertFalse(second_started.wait(0.2))
            allow_first_return.set()
            server.join()

        messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertIn({"id": "rollback-1", "event": "error", "code": "BUSY"}, messages)


if __name__ == "__main__":
    unittest.main()
