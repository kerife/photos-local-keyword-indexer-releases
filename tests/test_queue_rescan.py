from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from photos_indexer.queue_rescan import QueueRescanArtifactError, load_queue_rescan


def _write_rescan(
    root: Path,
    *,
    session_id: str,
    item_id: str,
    revision: int,
    decision_id: str,
    additional_information: str | None = "Colección deportiva local.",
    analysis_prompt: str | None = "Distingue cada animal visible.",
    reset_prompt: bool = False,
    reset_edits: bool = False,
) -> Path:
    path = root / session_id / "rescans" / f"{decision_id}.json"
    path.parent.mkdir(mode=0o700, parents=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps({
        "session_id": session_id,
        "item_id": item_id,
        "revision": revision,
        "decision_id": decision_id,
        "model": "qwen3-vl:8b",
        "profile": "free_local",
        "layers": {
            "places": False,
            "documents_text": True,
            "people_accessories": True,
            "semantic_normalization": False,
        },
        "additional_information": additional_information,
        "analysis_prompt": analysis_prompt,
        "reset_prompt": reset_prompt,
        "reset_edits": reset_edits,
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_load_queue_rescan_accepts_the_exact_private_schema(tmp_path: Path) -> None:
    session_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    _write_rescan(
        tmp_path,
        session_id=session_id,
        item_id=item_id,
        revision=2,
        decision_id=decision_id,
    )

    artifact = load_queue_rescan(
        tmp_path,
        session_id=session_id,
        item_id=item_id,
        revision=2,
        decision_id=decision_id,
    )

    assert artifact.model == "qwen3-vl:8b"
    assert artifact.profile == "free_local"
    assert artifact.layers.places is False
    assert artifact.analysis_prompt == "Distingue cada animal visible."
    assert artifact.reset_edits is False


@pytest.mark.parametrize(
    ("additional_information", "analysis_prompt", "reset_prompt"),
    [
        ("API token: private-value", None, False),
        (None, "Usa 19.4326,-99.1332 como referencia", False),
        (None, "Prompt personalizado", True),
    ],
)
def test_load_queue_rescan_rejects_sensitive_or_conflicting_content(
    tmp_path: Path,
    additional_information: str | None,
    analysis_prompt: str | None,
    reset_prompt: bool,
) -> None:
    session_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    _write_rescan(
        tmp_path,
        session_id=session_id,
        item_id=item_id,
        revision=1,
        decision_id=decision_id,
        additional_information=additional_information,
        analysis_prompt=analysis_prompt,
        reset_prompt=reset_prompt,
    )

    with pytest.raises(QueueRescanArtifactError):
        load_queue_rescan(
            tmp_path,
            session_id=session_id,
            item_id=item_id,
            revision=1,
            decision_id=decision_id,
        )
