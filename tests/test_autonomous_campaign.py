from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from photos_indexer.adapters import SelectedPhoto
from photos_indexer.queue_session import SessionConfig


CAMPAIGN = "campaign-one"
NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def records(count: int):
    for position in range(count):
        yield SelectedPhoto(f"{position:08d}-aaaa-4aaa-8aaa-aaaaaaaaaaaa/L0/001", datetime(2026, 9, 12))


def create_store(tmp_path: Path, *, count: int = 3, limit: int | None = None):
    from photos_indexer.autonomous_runtime import CampaignStore

    root = tmp_path / "autonomous-campaigns"
    root.mkdir(mode=0o700, exist_ok=True)
    return CampaignStore.create(
        root / CAMPAIGN,
        campaign_id=CAMPAIGN,
        config=SessionConfig(photo_count=2, inference_concurrency=2),
        records=records(count),
        limit=limit,
        created_at=NOW,
    )


def test_campaign_snapshot_is_capped_without_losing_invalid_inventory_count(tmp_path):
    """Using manual selection or counting invalid identities as attempts breaks this."""
    from photos_indexer.autonomous_runtime import CampaignStore

    root = tmp_path / "autonomous-campaigns"
    root.mkdir(mode=0o700)
    store = CampaignStore.create(
        root / CAMPAIGN,
        campaign_id=CAMPAIGN,
        config=SessionConfig(photo_count=2, inference_concurrency=2),
        records=[*records(601), None, None],
        limit=3,
        created_at=NOW,
    )
    assert store.snapshot.total_count == 601
    assert store.snapshot.chunk_count == 4
    assert store.status() == {
        "type": "autonomy_campaign", "campaign_id": CAMPAIGN, "revision": 0,
        "state": "paused", "total": 3, "examined": 0, "analyzed": 0,
        "saved": 0, "no_change": 0, "attention": 0, "remaining": 3,
        "in_flight": 0, "invalid_count": 2, "reason": "none",
    }
    assert len(list(store.pending())) == 3
    reopened = CampaignStore.load(store.path)
    assert reopened.status()["state"] == "paused"
    assert reopened.status()["reason"] == "recovered"
    assert reopened.snapshot.digest == store.snapshot.digest


@pytest.mark.parametrize("limit", [True, False, 0, -1, 2**31, "3"])
def test_invalid_caps_fail_before_snapshot_publication(tmp_path, limit):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    with pytest.raises(CampaignStorageError):
        create_store(tmp_path, limit=limit)
    assert not (tmp_path / "autonomous-campaigns" / CAMPAIGN).exists()


def control(store, decision="enable-one", command="autonomy_start"):
    payload = {"campaign_id": CAMPAIGN, "decision_id": decision}
    if command == "autonomy_start":
        payload.update(runs_root=str(store.path.parent.parent / "runs"), settings_path=str(store.path.parent.parent / "settings.json"), limit=store.metadata["limit"])
    return command, payload


def test_replayed_activation_never_turns_a_paused_campaign_back_on(tmp_path):
    store = create_store(tmp_path)
    assert store.authorize(*control(store), now=NOW) is not None
    assert store.status()["state"] == "running"
    store.pause(*control(store, "disable-one", "autonomy_pause"))
    assert store.authorize(*control(store), now=NOW) is None
    assert store.status()["state"] == "paused"
    assert store.authorize(*control(store, "resume-one", "autonomy_resume"), now=NOW) is not None
    assert store.status()["state"] == "running"


def test_control_identity_cannot_be_reused_for_another_action(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    store = create_store(tmp_path)
    store.authorize(*control(store), now=NOW)
    with pytest.raises(CampaignStorageError):
        store.pause(*control(store, "enable-one", "autonomy_pause"))


def test_out_of_order_outcomes_rebuild_counts_and_retain_unsettled_positions(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStore

    store = create_store(tmp_path, count=601)
    store.mark_examined(0)
    store.mark_examined(2)
    store.mark_examined(0)
    store.settle(2, "attention", reason="failed")
    store.settle(2, "attention", reason="failed")
    # A stale compact checkpoint must not skip a position or duplicate counts.
    path = store.path / "checkpoint.json"
    stale = json.loads(path.read_text())
    stale.update(examined=0, attention=0, remaining=601, in_flight=0)
    path.write_text(json.dumps(stale))
    reopened = CampaignStore.load(store.path)
    assert reopened.status()["examined"] == 2
    assert reopened.status()["attention"] == 1
    assert reopened.status()["remaining"] == 600
    assert reopened.status()["in_flight"] == 0
    pending = [position for position, _ in reopened.pending()]
    assert pending[:3] == [0, 1, 3]
    assert len(pending) == 600


def test_examined_capacity_and_cap_are_enforced_before_durable_admission(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    store = create_store(tmp_path, count=10, limit=3)
    store.mark_examined(0)
    store.mark_examined(1)
    with pytest.raises(CampaignStorageError):
        store.mark_examined(2)
    assert store.status()["examined"] == 2
    store.settle(0, "attention", reason="failed")
    store.mark_examined(2)
    with pytest.raises(CampaignStorageError):
        store.mark_examined(3)
    assert store.status()["examined"] == 3


@pytest.mark.parametrize("change", [
    {"limit": True}, {"campaign_id": "../escape"}, {"runs_root": "/tmp/runs"},
    {"prompt": "private"}, {"settings_path": 4}, {"decision_id": "bad/decision"},
])
def test_control_rejects_wrong_binding_types_and_content(tmp_path, change):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    store = create_store(tmp_path)
    command, payload = control(store)
    payload.update(change)
    with pytest.raises(CampaignStorageError):
        store.authorize(command, payload, now=NOW)
    assert list((store.path / "controls").iterdir()) == []
    assert store.status()["state"] == "paused"


def apply_setup(tmp_path, *, confidence=0.9, proposed=None, caption=None, current_caption="", errors=False):
    from photos_indexer.autonomous_runtime import CampaignStore, AutomaticApplyGate
    from photos_indexer.workflows import ApplyDependencies, run_apply
    from tests.test_workflows import make_photo, make_manifest, StatefulBridge, RevalidatingSelector

    photo = make_photo(1)
    if proposed is not None:
        photo.proposed_keywords = proposed
    photo.confidence = confidence
    if not photo.proposed_keywords:
        photo.scan_state = "noop"
    photo.proposed_caption = caption
    photo.caption_state = "proposed" if caption else "not_requested"
    if errors:
        photo.errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
    root = tmp_path / "autonomous-campaigns"
    root.mkdir(mode=0o700)
    store = CampaignStore.create(
        root / CAMPAIGN, campaign_id=CAMPAIGN,
        config=SessionConfig(photo_count=2, inference_concurrency=2),
        records=[SelectedPhoto(photo.photos_local_identifier, photo.date)],
        limit=None, created_at=NOW,
    )
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    source = make_manifest(runs / "source", [photo], scan_status="ready_with_errors" if errors else "ready")
    bridge = StatefulBridge({"local-1": ["PERRO", "Familia"]})
    bridge.descriptions["local-1"] = current_caption
    deps = ApplyDependencies(selector_factory=RevalidatingSelector, bridge_factory=lambda: bridge, global_lock_path=tmp_path / "apply.lock")
    gate = AutomaticApplyGate(store, bridge_factory=lambda: bridge, apply_runner=lambda path: run_apply(path, dependencies=deps))
    store.authorize(*control(store), now=NOW)
    store.mark_examined(0)
    return store, gate, source, bridge


@pytest.mark.parametrize("confidence, outcome", [(0.84, "attention"), (0.85, "saved"), (0.86, "saved"), (None, "attention")])
def test_threshold_uses_source_confidence_and_real_apply_preserves_existing_values(tmp_path, confidence, outcome):
    from photos_indexer.autonomous_runtime import autonomous_run_provenance
    from photos_indexer.manifest import load_manifest

    store, gate, source, bridge = apply_setup(tmp_path, confidence=confidence, current_caption="Mi texto")
    assert gate.process(0, source) == outcome
    assert store.status()[outcome] == 1
    assert store.status()["analyzed"] == 1
    assert store.status()["in_flight"] == 0
    assert bridge.descriptions["local-1"] == "Mi texto"
    if outcome == "saved":
        assert bridge.states["local-1"] == ["PERRO", "Familia", "playa"]
        reviews = list((tmp_path / "runs").glob("*-review-*/manifest.json"))
        assert len(reviews) == 1
        manifest = load_manifest(reviews[0].parent)
        assert manifest.photos[0].keyword_origins == {"playa": "model"}
        provenance = autonomous_run_provenance(reviews[0])
        assert provenance["origin"] == "autonomous_toggle"
        assert provenance["campaign_id"] == CAMPAIGN
        assert provenance["position"] == 0
        assert "human_review" not in provenance
    else:
        assert bridge.states["local-1"] == ["PERRO", "Familia"]
        assert list((store.path / "positions" / "0000000000").glob("decision.json")) == []


@pytest.mark.parametrize("proposed, caption, current_caption, outcome", [
    ([], None, "", "no_change"),
    ([], "Una playa visible.", "", "saved"),
    ([], "Una playa visible.", "Mi texto", "no_change"),
    (["playa"], "Una playa visible.", "Mi texto", "saved"),
])
def test_useful_only_and_caption_only_results(tmp_path, proposed, caption, current_caption, outcome):
    store, gate, source, bridge = apply_setup(tmp_path, proposed=proposed, caption=caption, current_caption=current_caption)
    assert gate.process(0, source) == outcome
    assert store.status()[outcome] == 1
    assert bridge.descriptions["local-1"] == (current_caption or caption or "")
    assert bool(list((tmp_path / "runs").glob("*-review-*"))) == (outcome == "saved")


def test_source_errors_are_not_automatic_write_eligibility(tmp_path):
    store, gate, source, bridge = apply_setup(tmp_path, errors=True)
    assert gate.process(0, source) == "attention"
    assert bridge.states["local-1"] == ["PERRO", "Familia"]
    assert store.status()["attention"] == 1


def test_pause_before_admission_retains_analysis_without_creating_decision(tmp_path):
    store, gate, source, bridge = apply_setup(tmp_path)
    store.pause(*control(store, "disable-one", "autonomy_pause"))
    assert gate.process(0, source) == "paused"
    assert store.status()["analyzed"] == 1
    assert store.status()["state"] == "paused"
    assert store.status()["in_flight"] == 0
    assert len(list(store.pending())) == 1
    assert bridge.states["local-1"] == ["PERRO", "Familia"]
    assert not (store.path / "positions" / "0000000000" / "decision.json").exists()


def test_reopen_recovers_receipt_and_never_calls_apply_again(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStore, AutomaticApplyGate

    store, gate, source, bridge = apply_setup(tmp_path)
    assert gate.process(0, source) == "saved"
    # Interruption after the workflow receipt but before the settled counter.
    (store.path / "positions" / "0000000000" / "outcome.json").unlink()
    reopened = CampaignStore.load(store.path)
    assert reopened.status()["saved"] == 1
    assert reopened.status()["analyzed"] == 1
    assert reopened.status()["state"] == "paused"
    assert list(reopened.pending()) == []

    def forbidden(*args):
        pytest.fail("accepted automatic decisions must never replay adapters")

    gate = AutomaticApplyGate(reopened, bridge_factory=forbidden, apply_runner=forbidden)
    assert gate.process(0, source) == "saved"


def test_uncertain_write_is_settled_and_not_retried(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStore, AutomaticApplyGate

    store, gate, source, bridge = apply_setup(tmp_path)
    bridge.fail_after_writing.add("local-1")
    assert gate.process(0, source) == "attention"
    assert store.status()["attention"] == 1
    assert store.status()["in_flight"] == 0
    assert len(bridge.replace_calls) == 1
    reopened = CampaignStore.load(store.path)
    reopened.authorize(*control(reopened, "resume", "autonomy_resume"), now=NOW)
    assert AutomaticApplyGate(reopened, bridge_factory=lambda: bridge).process(0, source) == "attention"
    assert len(bridge.replace_calls) == 1


@pytest.mark.parametrize("failure_file, writes, recovered", [
    ("decision.json", 0, None), ("reviewed.json", 0, "attention"),
    ("autonomy.json", 0, "attention"), ("outcome.json", 1, "saved"),
])
def test_evidence_storage_failure_closes_gate_and_recovery_never_reapplies(tmp_path, monkeypatch, failure_file, writes, recovered):
    import photos_indexer.autonomous_runtime as runtime

    store, gate, source, bridge = apply_setup(tmp_path)
    real_write = runtime._write

    def failing_write(path, value, **kwargs):
        if path.name == failure_file:
            raise runtime.CampaignStorageError("evidence unavailable")
        return real_write(path, value, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_write", failing_write)
        with pytest.raises(runtime.CampaignStorageError):
            gate.process(0, source)
    assert store.status()["reason"] == "storage"
    assert len(bridge.replace_calls) == writes
    reopened = runtime.CampaignStore.load(store.path)
    assert reopened.status()["state"] == "paused"
    if recovered is not None:
        assert reopened.status()[recovered] == 1
        assert list(reopened.pending()) == []
    else:
        assert len(list(reopened.pending())) == 1
    assert len(bridge.replace_calls) == writes


@pytest.mark.parametrize("in_source", [False, True])
def test_permission_loss_closes_whole_campaign_admission(tmp_path, monkeypatch, in_source):
    from photos_indexer.adapters import PhotosAccessError
    from photos_indexer.manifest import load_manifest, write_manifest

    store, gate, source, bridge = apply_setup(tmp_path)
    if in_source:
        manifest = load_manifest(source.parent)
        manifest.photos[0].errors = [{"stage": "metadata", "code": "PHOTOS_ACCESS_DENIED"}]
        manifest.scan_status = "ready_with_errors"
        write_manifest(source.parent, manifest)
    else:
        def denied(*args):
            raise PhotosAccessError()
        monkeypatch.setattr(bridge, "read", denied)
    assert gate.process(0, source) == "attention"
    assert store.status()["reason"] == "permission"
    assert store.status()["state"] == "paused"
    assert bridge.replace_calls == []


def test_pause_after_admission_finishes_the_accepted_write(tmp_path):
    from photos_indexer.service import review_manifest_v4

    store, gate, source, bridge = apply_setup(tmp_path)

    def pause_then_review(*args):
        assert (store.path / "positions" / "0000000000" / "decision.json").exists()
        store.pause(*control(store, "pause-during-review", "autonomy_pause"))
        return review_manifest_v4(*args)

    gate.review_runner = pause_then_review
    assert gate.process(0, source) == "saved"
    assert bridge.states["local-1"] == ["PERRO", "Familia", "playa"]
    assert store.status()["saved"] == 1


def test_pause_during_fresh_read_prevents_automatic_admission(tmp_path, monkeypatch):
    store, gate, source, bridge = apply_setup(tmp_path)
    real_read = bridge.read

    def read_then_pause(*args):
        result = real_read(*args)
        store.pause(*control(store, "pause-in-read", "autonomy_pause"))
        return result

    monkeypatch.setattr(bridge, "read", read_then_pause)
    assert gate.process(0, source) == "paused"
    assert bridge.replace_calls == []
    assert not (store.path / "positions" / "0000000000" / "decision.json").exists()


def test_history_rejects_a_changed_authorization_link(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStorageError, autonomous_run_provenance

    store, gate, source, bridge = apply_setup(tmp_path)
    assert gate.process(0, source) == "saved"
    reviewed = next((tmp_path / "runs").glob("*-review-*/manifest.json"))
    link_path = reviewed.parent / "autonomy.json"
    link = json.loads(link_path.read_text())
    link["authorization_digest"] = "0" * 64
    link_path.write_text(json.dumps(link))
    with pytest.raises(CampaignStorageError):
        autonomous_run_provenance(reviewed)


def test_storage_accepts_the_existing_ipc_opaque_identifier_alphabet(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStore

    root = tmp_path / "autonomous-campaigns"
    root.mkdir(mode=0o700)
    store = CampaignStore.create(
        root / "campaign.one", campaign_id="campaign.one",
        config=SessionConfig(photo_count=1, inference_concurrency=1),
        records=[], limit=None, created_at=NOW,
    )
    assert store.status()["campaign_id"] == "campaign.one"


@pytest.mark.parametrize("damage", ["missing", "malformed", "permissions", "digest"])
def test_unavailable_or_corrupt_source_evidence_closes_admission(tmp_path, damage):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    store, gate, source, bridge = apply_setup(tmp_path)
    if damage == "missing":
        source.unlink()
    elif damage == "malformed":
        source.write_text("{")
    elif damage == "permissions":
        source.chmod(0o644)
    else:
        value = json.loads(source.read_text())
        value["photos"][0]["title"] = "Changed after analysis"
        source.write_text(json.dumps(value))
    with pytest.raises(CampaignStorageError):
        gate.process(0, source)
    assert store.status()["reason"] == "storage"
    assert store.status()["state"] in {"paused", "pausing"}
    assert store.status()["attention"] == 0
    assert bridge.replace_calls == []
    assert not (store.path / "positions" / "0000000000" / "decision.json").exists()


def test_intact_but_mismatched_source_remains_a_per_position_rejection(tmp_path):
    from photos_indexer.manifest import load_manifest, write_manifest

    store, gate, source, bridge = apply_setup(tmp_path)
    value = load_manifest(source.parent)
    value.photos[0].photos_local_identifier = "different-local-id"
    write_manifest(source.parent, value)
    assert gate.process(0, source) == "attention"
    assert store.status()["reason"] == "none"
    assert store.status()["attention"] == 1
    assert bridge.replace_calls == []


@pytest.mark.parametrize("operation", ["examine", "analysis", "activate", "settle", "release"])
def test_store_mutation_checkpoint_failure_revokes_authorization_without_gate_wrapper(tmp_path, monkeypatch, operation):
    import photos_indexer.autonomous_runtime as runtime

    store, gate, source, bridge = apply_setup(tmp_path)
    if operation == "examine":
        store.release(0)
    real_write = runtime._write

    def no_checkpoint(path, value, **kwargs):
        if path.name == "checkpoint.json":
            raise OSError("checkpoint storage unavailable")
        return real_write(path, value, **kwargs)

    monkeypatch.setattr(runtime, "_write", no_checkpoint)
    with pytest.raises(runtime.CampaignStorageError):
        if operation == "examine":
            store.mark_examined(0)
        elif operation == "analysis":
            store.record_analysis(0, source)
        elif operation == "activate":
            store.authorize(*control(store, "resume-fails", "autonomy_resume"), now=NOW)
        elif operation == "settle":
            store.settle(0, "attention", reason="failed")
        else:
            store.release(0)
    assert store.status()["state"] == "paused"
    assert store.status()["reason"] == "storage"
    assert store._authorization is None
    assert bridge.replace_calls == []


def test_position_artifact_failure_also_revokes_authorization(tmp_path, monkeypatch):
    import photos_indexer.autonomous_runtime as runtime

    store = create_store(tmp_path)
    store.authorize(*control(store), now=NOW)
    real_write = runtime._write

    def no_admission(path, value, **kwargs):
        if path.name == "examined.json":
            raise runtime.CampaignStorageError("position storage unavailable")
        return real_write(path, value, **kwargs)

    monkeypatch.setattr(runtime, "_write", no_admission)
    with pytest.raises(runtime.CampaignStorageError):
        store.mark_examined(0)
    assert store.status()["reason"] == "storage"
    assert store._authorization is None


@pytest.mark.parametrize("position", [True, -1, 3, 2])
def test_validation_and_capacity_errors_do_not_revoke_authorization(tmp_path, position):
    from photos_indexer.autonomous_runtime import CampaignStorageError

    store = create_store(tmp_path)
    store.authorize(*control(store), now=NOW)
    store.mark_examined(0)
    store.mark_examined(1)
    with pytest.raises(CampaignStorageError):
        store.mark_examined(position)
    assert store.status()["state"] == "running"
    assert store.status()["reason"] == "none"
    assert store._authorization == "enable-one"


@pytest.mark.parametrize("damage", ["decision", "authorization", "receipt", "source"])
@pytest.mark.parametrize("operation", ["recover", "replay"])
def test_settled_saved_claim_requires_linkage_and_verified_receipt_without_adapters(tmp_path, damage, operation):
    import photos_indexer.autonomous_runtime as runtime
    from photos_indexer.manifest import load_manifest, write_manifest

    store, gate, source, bridge = apply_setup(tmp_path)
    assert gate.process(0, source) == "saved"
    reviewed = next((tmp_path / "runs").glob("*-review-*/manifest.json"))
    if damage == "decision":
        (store.path / "positions" / "0000000000" / "decision.json").unlink()
    elif damage == "authorization":
        (store.path / "authorizations" / "enable-one.json").write_text("{")
    elif damage == "source":
        source.unlink()
    else:
        manifest = load_manifest(reviewed.parent)
        manifest.photos[0].mutation_digest = None
        write_manifest(reviewed.parent, manifest)

    def forbidden(*args):
        pytest.fail("settled evidence validation must not call Photos or reapply")

    with pytest.raises(runtime.CampaignStorageError):
        if operation == "recover":
            runtime.CampaignStore.load(store.path)
        else:
            runtime.AutomaticApplyGate(store, bridge_factory=forbidden, apply_runner=forbidden).process(0, source)
    if operation == "replay":
        assert store.status()["reason"] == "storage"
        assert store._authorization is None
    assert len(bridge.replace_calls) == 1
