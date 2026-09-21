from __future__ import annotations

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_service_event


@pytest.mark.parametrize("exit_code", [1, 2])
def test_nonzero_completion_requires_a_stable_error_code(exit_code: int) -> None:
    """A failed terminal event must explain why and remain actionable."""
    with pytest.raises(IPCProtocolError):
        _safe_service_event({"type": "completed", "exit_code": exit_code, "next_action": "none"})


def test_photo_progress_rejects_cloud_model_names() -> None:
    """Progress events must preserve the same local-only model policy as preflight."""
    with pytest.raises(IPCProtocolError):
        _safe_service_event({
            "type": "photo_progress",
            "uuid": "aaaaaaaa",
            "state": "ready",
            "model_used": "qwen3-vl:4b-cloud",
            "model_reason": "single_policy",
            "keywords_count": 0,
        })


def test_photo_progress_rejects_unknown_states_before_reaching_swift() -> None:
    """The Python projection must match Swift's finite progress-state set."""
    with pytest.raises(IPCProtocolError):
        _safe_service_event({
            "type": "photo_progress",
            "uuid": "aaaaaaaa",
            "state": "invented_state",
            "keywords_count": 0,
        })
