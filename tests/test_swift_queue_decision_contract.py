from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from photos_indexer.queue_decision import QueueDecisionArtifact


def test_swift_queue_decisions_satisfy_the_strict_python_schema(tmp_path: Path) -> None:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is required for the Swift/Python contract test")

    project_root = Path(__file__).resolve().parents[1]
    main = tmp_path / "main.swift"
    executable = tmp_path / "queue-decision-contract"
    main.write_text(
        """
        import Foundation

        let decisions = [
            QueueReviewDecision(
                sessionID: "11111111-1111-1111-1111-111111111111",
                itemID: "22222222-2222-2222-2222-222222222222",
                revision: 2,
                decisionID: "33333333-3333-3333-3333-333333333333",
                approvedKeywords: ["gato", "perro"],
                approvedCaption: nil
            ),
            QueueReviewDecision(
                sessionID: "44444444-4444-4444-4444-444444444444",
                itemID: "55555555-5555-5555-5555-555555555555",
                revision: 3,
                decisionID: "66666666-6666-6666-6666-666666666666",
                approvedKeywords: [],
                approvedCaption: "Un gato descansa."
            ),
            QueueReviewDecision(
                sessionID: "77777777-7777-7777-7777-777777777777",
                itemID: "88888888-8888-8888-8888-888888888888",
                revision: 4,
                decisionID: "99999999-9999-9999-9999-999999999999",
                approvedKeywords: ["playa", "mar"],
                approvedCaption: "Una playa junto al mar."
            ),
        ]
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        for decision in decisions {
            let data = try encoder.encode(decision)
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data([0x0a]))
        }
        """,
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "clang-module-cache")
    subprocess.run(
        [
            swiftc,
            str(project_root / "app/PhotosLocalKeywordIndexer/Services/TrustedSystemPath.swift"),
            str(project_root / "app/PhotosLocalKeywordIndexer/Services/QueueDecisionStore.swift"),
            str(main),
            "-o",
            str(executable),
        ],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    completed = subprocess.run(
        [str(executable)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    artifacts = [
        QueueDecisionArtifact.from_dict(json.loads(line))
        for line in completed.stdout.splitlines()
    ]

    assert artifacts == [
        QueueDecisionArtifact(
            session_id="11111111-1111-1111-1111-111111111111",
            item_id="22222222-2222-2222-2222-222222222222",
            revision=2,
            decision_id="33333333-3333-3333-3333-333333333333",
            approved_keywords=("gato", "perro"),
            approved_caption=None,
        ),
        QueueDecisionArtifact(
            session_id="44444444-4444-4444-4444-444444444444",
            item_id="55555555-5555-5555-5555-555555555555",
            revision=3,
            decision_id="66666666-6666-6666-6666-666666666666",
            approved_keywords=(),
            approved_caption="Un gato descansa.",
        ),
        QueueDecisionArtifact(
            session_id="77777777-7777-7777-7777-777777777777",
            item_id="88888888-8888-8888-8888-888888888888",
            revision=4,
            decision_id="99999999-9999-9999-9999-999999999999",
            approved_keywords=("playa", "mar"),
            approved_caption="Una playa junto al mar.",
        ),
    ]
