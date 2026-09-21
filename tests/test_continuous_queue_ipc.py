from __future__ import annotations

import json
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from photos_indexer.adapters import PhotosAccessError
from photos_indexer.ipc import (
    IPCProtocolError,
    QueueDecisionInvalidError,
    _safe_service_event,
    parse_request_line,
    serve,
)
from photos_indexer.queue_decision import QueueDecisionArtifactError


def _line(command: str, payload: dict[str, object]) -> str:
    return json.dumps({"id": "request-1", "command": command, "payload": payload})


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            "queue_start",
            {
                "session_id": "session-1",
                "revision": 0,
                "decision_id": "decision-1",
                "runs_root": "/private/runs",
                "settings_path": "/private/session-settings.json",
            },
        ),
        (
            "queue_update",
            {
                "session_id": "session-1",
                "revision": 3,
                "decision_id": "decision-2",
                "settings_path": "/private/session-settings.json",
            },
        ),
        (
            "queue_persist",
            {
                "session_id": "session-1",
                "item_id": "item-1",
                "revision": 4,
                "decision_id": "decision-3",
            },
        ),
        (
            "queue_discard",
            {
                "session_id": "session-1",
                "item_id": "item-1",
                "revision": 4,
                "decision_id": "decision-4",
            },
        ),
        (
            "queue_rescan",
            {
                "session_id": "session-1",
                "item_id": "item-1",
                "revision": 4,
                "decision_id": "decision-5",
            },
        ),
        (
            "queue_pause",
            {"session_id": "session-1", "revision": 5, "decision_id": "decision-6"},
        ),
        (
            "queue_resume",
            {"session_id": "session-1", "revision": 6, "decision_id": "decision-7"},
        ),
        (
            "queue_stop",
            {"session_id": "session-1", "revision": 7, "decision_id": "decision-8"},
        ),
    ],
)
def test_queue_commands_accept_only_referenced_private_artifacts(
    command: str,
    payload: dict[str, object],
) -> None:
    request = parse_request_line(_line(command, payload))

    assert request.command == command
    assert request.payload == payload


@pytest.mark.parametrize("field", ["keywords", "caption", "prompt", "coordinates", "image"])
def test_queue_commands_reject_sensitive_inline_content(field: str) -> None:
    payload: dict[str, object] = {
        "session_id": "session-1",
        "item_id": "item-1",
        "revision": 4,
        "decision_id": "decision-3",
        field: "private content",
    }

    with pytest.raises(IPCProtocolError):
        parse_request_line(_line("queue_persist", payload))


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "session-1", "item_id": "item-1", "revision": True, "decision_id": "decision-1"},
        {"session_id": "session-1", "item_id": "item-1", "revision": -1, "decision_id": "decision-1"},
        {"session_id": "session-1", "item_id": "item-1", "revision": 1},
        {"session_id": [], "item_id": "item-1", "revision": 1, "decision_id": "decision-1"},
    ],
)
def test_queue_commands_reject_invalid_revision_or_identity(payload: dict[str, object]) -> None:
    with pytest.raises(IPCProtocolError):
        parse_request_line(_line("queue_persist", payload))


def test_queue_events_project_only_state_and_private_artifact_references() -> None:
    assert _safe_service_event(
        {
            "type": "queue_session",
            "session_id": "session-1",
            "revision": 7,
            "state": "running",
            "queued": 3,
            "analyzing": 2,
            "ready": 4,
            "save_queued": 1,
            "saving": 2,
            "saved": 8,
            "attention": 1,
        }
    ) == {
        "event": "queue_session",
        "session_id": "session-1",
        "revision": 7,
        "state": "running",
        "queued": 3,
        "analyzing": 2,
        "ready": 4,
        "save_queued": 1,
        "saving": 2,
        "saved": 8,
        "attention": 1,
    }
    assert _safe_service_event(
        {
            "type": "queue_item",
            "session_id": "session-1",
            "item_id": "item-1",
            "revision": 4,
            "state": "ready",
            "manifest": "/private/runs/run-1/manifest.json",
        }
    )["event"] == "queue_item"


def test_autonomy_activity_projects_only_ephemeral_photo_state() -> None:
    event = {
        "type": "autonomy_activity",
        "campaign_id": "campaign-1",
        "revision": 4,
        "position": 12,
        "state": "analyzing",
        "photos_local_identifier": "A1B2/C3D4",
    }

    assert _safe_service_event(event) == {
        "event": "autonomy_activity",
        "campaign_id": "campaign-1",
        "revision": 4,
        "position": 12,
        "state": "analyzing",
        "photos_local_identifier": "A1B2/C3D4",
    }


@pytest.mark.parametrize(
    "field",
    ["keywords", "caption", "coordinates", "manifest", "title", "uuid"],
)
def test_autonomy_activity_rejects_content_and_artifact_fields(field: str) -> None:
    event = {
        "type": "autonomy_activity",
        "campaign_id": "campaign-1",
        "revision": 4,
        "position": 12,
        "state": "analyzing",
        "photos_local_identifier": "A1B2/C3D4",
        field: "private content",
    }

    with pytest.raises(IPCProtocolError):
        _safe_service_event(event)


def test_queue_session_counters_support_long_running_sessions() -> None:
    projected = _safe_service_event(
        {
            "type": "queue_session",
            "session_id": "session-1",
            "revision": 2_001,
            "state": "running",
            "queued": 0,
            "analyzing": 0,
            "ready": 0,
            "save_queued": 0,
            "saving": 0,
            "saved": 1_000,
            "attention": 0,
        }
    )

    assert projected["saved"] == 1_000
    assert projected["save_queued"] == 0
    assert projected["saving"] == 0


@pytest.mark.parametrize("field", ["keywords", "caption", "prompt", "coordinates", "image"])
def test_queue_events_reject_sensitive_inline_content(field: str) -> None:
    with pytest.raises(IPCProtocolError):
        _safe_service_event(
            {
                "type": "queue_item",
                "session_id": "session-1",
                "item_id": "item-1",
                "revision": 4,
                "state": "ready",
                field: "private content",
            }
        )


def test_queue_controls_are_multiplexed_while_the_session_is_active() -> None:
    start = {
        "id": "start-1",
        "command": "queue_start",
        "payload": {
            "session_id": "session-1",
            "revision": 0,
            "decision_id": "decision-1",
            "runs_root": "/private/runs",
            "settings_path": "/private/settings.json",
        },
    }
    pause = {
        "id": "pause-1",
        "command": "queue_pause",
        "payload": {"session_id": "session-1", "revision": 1, "decision_id": "decision-2"},
    }
    outgoing = io.StringIO()

    def handler(request, emit) -> None:
        emit({
            "type": "queue_session",
            "session_id": request.payload["session_id"],
            "revision": request.payload["revision"],
            "state": "paused" if request.command == "queue_pause" else "running",
        })

    with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/private/runs")):
        serve(
            io.StringIO(json.dumps(start) + "\n" + json.dumps(pause) + "\n"),
            outgoing,
            queue_handler=handler,
        )

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert not any(message.get("code") == "BUSY" for message in messages)
    assert any(message.get("id") == "start-1" and message.get("event") == "queue_session" for message in messages)
    assert any(message.get("id") == "pause-1" and message.get("event") == "queue_session" for message in messages)
    assert any(message.get("id") == "pause-1" and message.get("event") == "completed" for message in messages)


def test_queue_resume_can_recover_when_the_helper_has_no_active_runtime() -> None:
    resume = {
        "id": "resume-1",
        "command": "queue_resume",
        "payload": {"session_id": "session-1", "revision": 7, "decision_id": "decision-1"},
    }
    outgoing = io.StringIO()
    handled: list[str] = []

    def handler(request, emit) -> None:
        handled.append(request.command)
        emit({
            "type": "queue_session",
            "session_id": request.payload["session_id"],
            "revision": request.payload["revision"],
            "state": "running",
        })

    with (
        patch("photos_indexer.ipc.app_runs_root", return_value=Path("/private/runs")),
        patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/private/runs")),
    ):
        serve(io.StringIO(json.dumps(resume) + "\n"), outgoing, queue_handler=handler)

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert handled == ["queue_resume"]
    assert not any(message.get("code") == "QUEUE_NOT_RUNNING" for message in messages)
    assert any(message.get("id") == "resume-1" and message.get("event") == "queue_session" for message in messages)
    assert any(message.get("id") == "resume-1" and message.get("event") == "completed" for message in messages)


def test_queue_start_preserves_photos_access_error_code() -> None:
    start = {
        "id": "start-photos-denied",
        "command": "queue_start",
        "payload": {
            "session_id": "session-1",
            "revision": 0,
            "decision_id": "decision-1",
            "runs_root": "/private/runs",
            "settings_path": "/private/settings.json",
        },
    }
    outgoing = io.StringIO()

    def handler(request, emit) -> None:
        raise PhotosAccessError()

    with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/private/runs")):
        serve(
            io.StringIO(json.dumps(start) + "\n"),
            outgoing,
            queue_handler=handler,
        )

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert any(
        message.get("id") == "start-photos-denied"
        and message.get("event") == "error"
        and message.get("code") == "PHOTOS_ACCESS_DENIED"
        for message in messages
    )
    assert not any(message.get("code") == "QUEUE_OPERATION_FAILED" for message in messages)


def test_queue_persist_maps_pre_admission_decision_rejection_to_dedicated_code() -> None:
    outgoing = io.StringIO()

    def handler(request, emit) -> None:
        del emit
        if request.command == "queue_persist":
            raise QueueDecisionInvalidError("queue decision artifact is invalid")

    start = {
        "id": "start-1",
        "command": "queue_start",
        "payload": {
            "session_id": "session-1",
            "revision": 0,
            "decision_id": "decision-1",
            "runs_root": "/private/runs",
            "settings_path": "/private/settings.json",
        },
    }
    persist = {
        "id": "persist-1",
        "command": "queue_persist",
        "payload": {
            "session_id": "session-1",
            "item_id": "item-1",
            "revision": 1,
            "decision_id": "decision-2",
        },
    }

    with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/private/runs")):
        serve(
            io.StringIO(json.dumps(start) + "\n" + json.dumps(persist) + "\n"),
            outgoing,
            queue_handler=handler,
        )

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert any(
        message.get("id") == "persist-1"
        and message.get("event") == "error"
        and message.get("code") == "QUEUE_DECISION_INVALID"
        for message in messages
    )
    assert not any(
        message.get("id") == "persist-1"
        and message.get("code") == "QUEUE_OPERATION_FAILED"
        for message in messages
    )


def test_queue_persist_keeps_non_boundary_decision_artifact_error_generic() -> None:
    outgoing = io.StringIO()

    def handler(request, emit) -> None:
        del emit
        if request.command == "queue_persist":
            raise QueueDecisionArtifactError("late queue failure")

    start = {
        "id": "start-1",
        "command": "queue_start",
        "payload": {
            "session_id": "session-1",
            "revision": 0,
            "decision_id": "decision-1",
            "runs_root": "/private/runs",
            "settings_path": "/private/settings.json",
        },
    }
    persist = {
        "id": "persist-1",
        "command": "queue_persist",
        "payload": {
            "session_id": "session-1",
            "item_id": "item-1",
            "revision": 1,
            "decision_id": "decision-2",
        },
    }

    with patch("photos_indexer.ipc.validate_app_runs_root", return_value=Path("/private/runs")):
        serve(
            io.StringIO(json.dumps(start) + "\n" + json.dumps(persist) + "\n"),
            outgoing,
            queue_handler=handler,
        )

    messages = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert any(
        message.get("id") == "persist-1"
        and message.get("event") == "error"
        and message.get("code") == "QUEUE_OPERATION_FAILED"
        for message in messages
    )
    assert not any(message.get("code") == "QUEUE_DECISION_INVALID" for message in messages)
