import io
import json

import pytest

from photos_indexer.ipc import IPCProtocolError, MAX_LINE_BYTES, _safe_service_event, parse_request_line, serve


def test_request_with_unpaired_unicode_surrogate_is_rejected_as_protocol_error():
    line = '{"id":"request-1","command":"cancel","payload":{}}\ud800'

    with pytest.raises(IPCProtocolError):
        parse_request_line(line)


def test_completed_manifest_with_unpaired_surrogate_is_rejected_before_serialization():
    event = {
        "type": "completed",
        "exit_code": 0,
        "manifest": "/safe/\ud800/manifest.json",
        "next_action": "none",
    }

    with pytest.raises(IPCProtocolError):
        _safe_service_event(event)


def test_deeply_nested_json_is_rejected_as_protocol_error():
    line = '{"id":"request-1","command":"cancel","payload":' + ("[" * 2000) + ("]" * 2000) + "}"

    with pytest.raises(IPCProtocolError):
        parse_request_line(line)


def test_duplicate_request_keys_are_rejected_as_protocol_error():
    line = '{"id":"request-1","command":"scan","command":"cancel","payload":{}}'

    with pytest.raises(IPCProtocolError):
        parse_request_line(line)


def test_serve_reads_stdin_with_a_bounded_line_size_and_continues_after_oversize():
    class BoundedReader:
        def __init__(self, value):
            self._stream = io.StringIO(value)
            self.sizes = []

        def readline(self, size=-1):
            self.sizes.append(size)
            return self._stream.readline(size)

    valid = '{"id":"request-2","command":"cancel","payload":{}}\n'
    incoming = BoundedReader("x" * (MAX_LINE_BYTES * 4) + "\n" + valid)
    outgoing = io.StringIO()

    serve(incoming, outgoing)

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert messages[0] == {"id": "invalid", "event": "error", "code": "INVALID_REQUEST"}
    assert messages[1] == {
        "id": "request-2",
        "event": "completed",
        "exit_code": 0,
        "next_action": "none",
    }
    assert incoming.sizes
    assert all(size == MAX_LINE_BYTES + 1 for size in incoming.sizes)
