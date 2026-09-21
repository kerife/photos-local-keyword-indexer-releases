import json

import pytest

from photos_indexer.ipc import IPCProtocolError, parse_request_line


def test_scan_model_policy_must_be_a_string_before_membership_check():
    line = (
        '{"id":"scan-1","command":"scan","payload":'
        '{"runs_root":"/safe/runs","model_policy":[]}}'
    )

    with pytest.raises(IPCProtocolError):
        parse_request_line(line)


@pytest.mark.parametrize("field", ["fast_model", "detailed_model"])
def test_scan_required_model_fields_must_not_be_null(field):
    line = json.dumps({
        "id": "scan-1",
        "command": "scan",
        "payload": {"runs_root": "/safe/runs", field: None},
    })

    with pytest.raises(IPCProtocolError):
        parse_request_line(line)
