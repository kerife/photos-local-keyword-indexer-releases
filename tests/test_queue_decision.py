from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from photos_indexer.queue_decision import QueueDecisionArtifact, QueueDecisionArtifactError, load_queue_decision


def _decision(*, session_id: str, item_id: str, decision_id: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "item_id": item_id,
        "revision": 2,
        "decision_id": decision_id,
        "approved_keywords": ["gato", "perro"],
        "approved_caption": "Un gato y un perro descansan juntos.",
    }


def _write_private(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def test_load_queue_decision_uses_only_the_expected_private_artifact(tmp_path: Path) -> None:
    session_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    path = tmp_path / session_id / "decisions" / f"{decision_id}.json"
    _write_private(path, _decision(session_id=session_id, item_id=item_id, decision_id=decision_id))

    restored = load_queue_decision(
        tmp_path,
        session_id=session_id,
        item_id=item_id,
        revision=2,
        decision_id=decision_id,
    )

    assert restored == QueueDecisionArtifact(
        session_id=session_id,
        item_id=item_id,
        revision=2,
        decision_id=decision_id,
        approved_keywords=("gato", "perro"),
        approved_caption="Un gato y un perro descansan juntos.",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra="private"),
        lambda value: value.update(item_id=str(uuid.uuid4())),
        lambda value: value.update(revision=True),
        lambda value: value.update(approved_keywords=["x"] * 9),
        lambda value: value.update(approved_caption="x" * 241),
        lambda value: value.update(approved_caption="bad\ud800"),
    ],
)
def test_load_queue_decision_rejects_malformed_or_mismatched_content(
    tmp_path: Path,
    mutation,
) -> None:
    session_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    value = _decision(session_id=session_id, item_id=item_id, decision_id=decision_id)
    mutation(value)
    path = tmp_path / session_id / "decisions" / f"{decision_id}.json"
    _write_private(path, value)

    with pytest.raises(QueueDecisionArtifactError):
        load_queue_decision(
            tmp_path,
            session_id=session_id,
            item_id=item_id,
            revision=2,
            decision_id=decision_id,
        )


def test_load_queue_decision_rejects_a_hardlinked_artifact(tmp_path: Path) -> None:
    session_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    path = tmp_path / session_id / "decisions" / f"{decision_id}.json"
    _write_private(path, _decision(session_id=session_id, item_id=item_id, decision_id=decision_id))
    os.link(path, tmp_path / "alias.json")

    with pytest.raises(QueueDecisionArtifactError):
        load_queue_decision(
            tmp_path,
            session_id=session_id,
            item_id=item_id,
            revision=2,
            decision_id=decision_id,
        )
