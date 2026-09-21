from __future__ import annotations

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_preflight_details
from photos_indexer.service import support_snapshot
from photos_indexer.workflows import WorkflowResult


@pytest.mark.parametrize("instruction", [
    "ollama pull ../private-model",
    "ollama pull models/../../private-model",
])
def test_preflight_projection_rejects_path_like_install_hints(instruction: str) -> None:
    with pytest.raises(IPCProtocolError):
        _safe_preflight_details({"safe_instruction": instruction})


def test_support_snapshot_does_not_forward_path_like_install_hint() -> None:
    snapshot = support_snapshot(WorkflowResult(
        exit_code=2,
        error_codes=("OLLAMA_MODEL_MISSING",),
        safe_instruction="ollama pull ../private-model",
    ))

    assert "safe_instruction" not in snapshot

