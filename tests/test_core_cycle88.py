from __future__ import annotations

from photos_indexer.service import support_snapshot
from photos_indexer.workflows import WorkflowResult

from tests.test_service_api import _manifest


def test_support_snapshot_preserves_a_valid_ollama_prerelease_version() -> None:
    manifest = _manifest()
    manifest.model["ollama_version"] = "0.12.8-rc.1+build.4"
    manifest.scan_digest = manifest.compute_scan_digest()

    snapshot = support_snapshot(WorkflowResult(exit_code=0, manifest=manifest))

    assert snapshot["model"]["ollama_version"] == "0.12.8-rc.1+build.4"
