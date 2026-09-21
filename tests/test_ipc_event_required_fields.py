import io
import json
from pathlib import Path
from unittest import mock

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_service_event, serve
from photos_indexer.workflows import WorkflowResult


def test_service_event_rejects_non_mapping_without_leaking_attribute_error():
    with pytest.raises(IPCProtocolError):
        _safe_service_event(None)


def test_worker_reports_non_mapping_handler_event_as_unsafe_workflow_result():
    """An injected handler must not downgrade an invalid event to a generic failure."""
    incoming = io.StringIO(
        '{"id":"scan-invalid-event","command":"scan","payload":'
        '{"limit":1,"runs_root":"/runs"}}\n'
    )
    outgoing = io.StringIO()

    def scan_handler(payload, emit, token):
        del payload, token
        emit(None)  # type: ignore[arg-type]
        return WorkflowResult(exit_code=0, next_action="none")

    with mock.patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/runs")):
        serve(incoming, outgoing, scan_handler=scan_handler)

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert {"id": "scan-invalid-event", "event": "error", "code": "UNSAFE_WORKFLOW_RESULT"} in messages
    assert "WORKER_OPERATION_FAILED" not in outgoing.getvalue()


@pytest.mark.parametrize(
    "event",
    [
        {"type": "started"},
        {"type": "photo_progress", "keywords_count": 1},
        {"type": "completed", "exit_code": 0},
    ],
)
def test_service_event_projection_rejects_missing_required_fields(event):
    """Malformed terminal/progress events must not reach the native client."""
    with pytest.raises(IPCProtocolError):
        _safe_service_event(event)
