from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photos_indexer.adapters import (
    OllamaEndpointUnavailableError,
    OllamaNoVisionError,
    OllamaVersionTooOldError,
)
from photos_indexer.ipc import (
    IPCProtocolError,
    _default_preflight,
    _safe_preflight_details,
    _safe_service_event,
    parse_request_line,
)
from photos_indexer.service import (
    CancellationToken,
    ScanRequest,
    _emit,
    app_runs_root,
    scan,
    validate_app_manifest_path,
    validate_app_runs_root,
)


def _request(command: str, payload: object, request_id: str = "x") -> str:
    return json.dumps({"id": request_id, "command": command, "payload": payload})


class ServiceBoundaryContractTests(unittest.TestCase):
    def test_runs_root_validation_rejects_relative_outside_and_non_directory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            root = app_runs_root(home)
            with self.assertRaises(ValueError):
                validate_app_runs_root(Path("relative"), home=home)
            with self.assertRaises(ValueError):
                validate_app_runs_root(Path(tmp) / "outside", home=home)
            root.parent.mkdir(parents=True)
            root.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_app_runs_root(root, home=home, create=False)

    def test_manifest_path_validation_rejects_scope_nesting_and_missing_or_unsafe_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            root = app_runs_root(home)
            root.mkdir(parents=True)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(root / "run" / "nested" / "manifest.json", home=home)
            run_dir = root / "run"
            run_dir.mkdir(mode=0o700)
            run_dir.chmod(0o700)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(run_dir / "manifest.json", home=home)
            run_dir.chmod(0o755)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(run_dir / "manifest.json", home=home)

    def test_scan_request_and_service_short_circuit_invalid_or_cancelled_operations(self) -> None:
        with self.assertRaises(ValueError):
            ScanRequest(Path("runs"), model_policy="invalid").selected_model()
        for invalid_policy in ([], {}):
            with self.subTest(invalid_policy=invalid_policy), self.assertRaises(ValueError):
                ScanRequest(Path("runs"), model_policy=invalid_policy).selected_model()  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ScanRequest(Path("runs"), model_policy="adaptive", model="vision:latest").selected_model()

        events: list[dict[str, object]] = []
        token = CancellationToken()
        token.cancel()
        called = False

        def runner(*args: object, **kwargs: object) -> object:
            nonlocal called
            called = True
            return object()

        result = scan(ScanRequest(Path("runs")), events.append, cancellation=token, scan_runner=runner)  # type: ignore[arg-type]
        self.assertEqual(result.error_codes, ("CANCELLED",))
        self.assertFalse(called)
        self.assertEqual(events[-1]["type"], "completed")

    def test_scan_request_rejects_non_boolean_privacy_options_before_workflow(self) -> None:
        """Direct service callers must not silently enable location or captions."""
        for field in ("apple_maps", "include_caption"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ScanRequest(Path("runs"), **{field: 1})

    def test_scan_request_rejects_invalid_limits_before_workflow(self) -> None:
        for limit in (True, 0, 501, [], {}):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                ScanRequest(Path("runs"), limit=limit)  # type: ignore[arg-type]

    def test_service_event_projection_rejects_unknown_shape(self) -> None:
        with self.assertRaises(ValueError):
            _emit(lambda event: None, {"type": "unknown"})
        with self.assertRaises(ValueError):
            _emit(lambda event: None, {"type": []})  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            _emit(lambda event: None, {"type": "started", "operation": "scan", "caption": "private"})


class IPCPayloadContractTests(unittest.TestCase):
    def assertInvalid(self, command: str, payload: object) -> None:
        with self.assertRaises(IPCProtocolError):
            parse_request_line(_request(command, payload))

    def test_payload_validation_rejects_invalid_shapes_and_types(self) -> None:
        self.assertInvalid("cancel", {"unexpected": True})
        for payload in ({}, {"models": "vision:latest"}, {"models": []}, {"models": ["a"] * 5}, {"models": [1]}, {"models": ["bad name"]}):
            self.assertInvalid("preflight", payload)

        self.assertInvalid("scan", {"runs_root": "/runs", "extra": True})
        self.assertInvalid("scan", {})
        self.assertInvalid("scan", {"runs_root": "/runs", "limit": 0})
        self.assertInvalid("scan", {"runs_root": "/runs", "model_policy": "bad"})
        self.assertInvalid("scan", {"runs_root": "/runs", "model": 1})
        self.assertInvalid("scan", {"runs_root": "/runs", "model": "bad name"})
        self.assertInvalid("scan", {"runs_root": "/runs", "apple_maps": 1})
        self.assertInvalid("scan", {"runs_root": "/runs", "include_caption": 1})
        self.assertInvalid("scan", {"runs_root": 1})

        self.assertInvalid("review", {"manifest": "/runs/manifest.json"})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": []})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {1: []}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {"bad": []}})
        valid_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {valid_uuid: "bad"}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {valid_uuid: ["x"] * 9}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {valid_uuid: ["x", "x"]}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {}, "caption_selections": []})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {}, "caption_selections": {1: True}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {}, "caption_selections": {"bad": True}})
        self.assertInvalid("review", {"manifest": "/runs/manifest.json", "selections": {}, "caption_selections": {valid_uuid: 1}})
        self.assertInvalid("apply", {})
        self.assertInvalid("rollback", {"manifest": ""})

    def test_service_event_projection_rejects_remaining_invalid_values(self) -> None:
        invalid_events = (
            {"type": "started", "operation": "bad operation"},
            {"type": "photo_progress", "uuid": "a", "state": "bad state"},
            {"type": "photo_progress", "uuid": "a", "state": "ready", "model_used": "bad model"},
            {"type": "photo_progress", "uuid": "a", "state": "ready", "model_reason": "bad reason"},
            {"type": "photo_progress", "uuid": "a", "state": "ready", "keywords_count": 1.0},
            {"type": "completed", "exit_code": 3, "next_action": "none"},
            {"type": "completed", "warning_codes": ["bad code"], "next_action": "none"},
            {"type": "completed", "next_action": "bad action"},
        )
        for event in invalid_events:
            with self.subTest(event=event), self.assertRaises(IPCProtocolError):
                _safe_service_event(event)

    def test_service_event_projection_rejects_failure_action_on_success(self) -> None:
        events: list[dict[str, object]] = []

        _emit(events.append, {"type": "completed", "exit_code": 0, "next_action": "retry_failed_operation"})

        self.assertEqual(events, [{
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        }])

        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "completed", "exit_code": 0, "next_action": "retry_failed_operation"})

    def test_service_event_projection_rejects_unhashable_success_action(self) -> None:
        events: list[dict[str, object]] = []

        _emit(events.append, {"type": "completed", "exit_code": 0, "next_action": []})

        self.assertEqual(events, [{
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        }])

        with self.assertRaises(IPCProtocolError):
            _safe_service_event({"type": "completed", "exit_code": 0, "next_action": []})

    def test_preflight_projection_rejects_remaining_untrusted_details(self) -> None:
        invalid_details = (
            None,
            {"unexpected": True},
            {"models": []},
            {"models": [] * 5},
            {"models": {1: "0.1.0"}},
            {"models": {"bad name": "0.1.0"}},
            {"models": {"vision:latest": "not-a-version"}},
            {"error_codes": "bad"},
            {"error_codes": ["bad code"]},
            {"safe_instruction": "rm -rf"},
            {"next_action": "bad action"},
        )
        for details in invalid_details:
            with self.subTest(details=details), self.assertRaises(IPCProtocolError):
                _safe_preflight_details(details)  # type: ignore[arg-type]

    def test_default_preflight_maps_local_failures_without_raw_details(self) -> None:
        from photos_indexer.adapters import AdapterError

        failures = (
            OllamaEndpointUnavailableError(),
            OllamaVersionTooOldError(),
            OllamaNoVisionError(),
            AdapterError("private server response"),
        )
        for failure in failures:
            class FailingVision:
                def check_model(self, model: str) -> str:
                    del model
                    raise failure

            with self.subTest(failure=type(failure).__name__), patch("photos_indexer.ipc.OllamaVisionClient", return_value=FailingVision()):
                code, details = _default_preflight({"models": ["vision:latest"]}, CancellationToken())
                self.assertEqual(code, 2)
                self.assertNotIn("private server response", json.dumps(details))

    def test_default_preflight_honors_cancellation_before_contacting_ollama(self) -> None:
        token = CancellationToken()
        token.cancel()
        with patch("photos_indexer.ipc.OllamaVisionClient") as client:
            code, details = _default_preflight({"models": ["vision:latest"]}, token)
        self.assertEqual((code, details), (1, {"error_codes": ["CANCELLED"], "next_action": "none"}))
        client.assert_not_called()

    def test_default_preflight_honors_cancellation_before_loading_photoscript(self) -> None:
        """Cancelling after Ollama must skip the local AppleScript compile check."""
        token = CancellationToken()

        class CancellingVision:
            def check_model(self, model: str) -> str:
                if model != "vision:latest":
                    raise AssertionError("unexpected model")
                token.cancel()
                return "0.12.7"
        with (
            patch("photos_indexer.ipc.OllamaVisionClient", return_value=CancellingVision()),
            patch("photos_indexer.ipc._photoscript_preflight") as photoscript_preflight,
        ):
            code, details = _default_preflight({"models": ["vision:latest"]}, token)

        self.assertEqual((code, details), (1, {"error_codes": ["CANCELLED"], "next_action": "none"}))
        photoscript_preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
