from __future__ import annotations

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_preflight_details


def test_preflight_projection_rejects_cloud_model_inventory_names() -> None:
    """IPC must not advertise a model that the local-only policy forbids."""
    with pytest.raises(IPCProtocolError):
        _safe_preflight_details({"models": {"vision:cloud": "0.12.7"}})

