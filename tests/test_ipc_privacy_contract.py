from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_service_event


@pytest.mark.parametrize("model_used", ["/Users/private/model", "https://example.invalid/model"])
def test_progress_rejects_path_or_url_as_model_name(model_used: str) -> None:
    """Progress metadata must not become a private-path or URL channel."""
    with pytest.raises(IPCProtocolError):
        _safe_service_event({
            "type": "photo_progress",
            "uuid": "aaaaaaaa",
            "state": "ready",
            "model_used": model_used,
        })


def test_completed_event_rejects_existing_manifest_symlink_or_hardlink() -> None:
    """IPC must not expose a durable path that resolves outside the run."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        runs_root = root / "runs"
        run_dir = runs_root / "run"
        run_dir.mkdir(mode=0o700, parents=True)
        os.chmod(runs_root, 0o700)
        outside = root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        outside.chmod(0o600)

        symlink = run_dir / "manifest.json"
        symlink.symlink_to(outside)
        with pytest.raises(IPCProtocolError):
            _safe_service_event(
                {
                    "type": "completed",
                    "exit_code": 0,
                    "manifest": str(symlink),
                    "next_action": "review_then_apply",
                },
                manifest_root=runs_root,
            )
        symlink.unlink()

        regular = run_dir / "regular.json"
        regular.write_text("{}", encoding="utf-8")
        regular.chmod(0o600)
        hardlink = run_dir / "manifest.json"
        os.link(regular, hardlink)
        assert stat.S_ISREG(hardlink.stat().st_mode)
        with pytest.raises(IPCProtocolError):
            _safe_service_event(
                {
                    "type": "completed",
                    "exit_code": 0,
                    "manifest": str(hardlink),
                    "next_action": "review_then_apply",
                },
                manifest_root=runs_root,
            )


def test_completed_event_rejects_a_missing_manifest_under_the_requested_root() -> None:
    """Swift must never receive a successful run whose review artifact is absent."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        runs_root = Path(temporary_directory) / "runs"
        runs_root.mkdir(mode=0o700)
        missing_manifest = runs_root / "phantom-run" / "manifest.json"

        with pytest.raises(IPCProtocolError):
            _safe_service_event(
                {
                    "type": "completed",
                    "exit_code": 0,
                    "manifest": str(missing_manifest),
                    "next_action": "review_then_apply",
                },
                manifest_root=runs_root,
            )

        assert not missing_manifest.exists()
