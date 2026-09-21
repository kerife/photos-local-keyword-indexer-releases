"""Offline native-protocol coverage for autonomous screenshot persistence."""

from functools import partial
import json

import pytest

from photos_indexer import adapters, workflows
from photos_indexer.autonomous_runtime import AutomaticApplyGate, CampaignStorageError, CampaignStore
from photos_indexer.queue_session import SessionConfig
from tests.test_autonomous_campaign import CAMPAIGN, NOW, control
from tests.test_autonomous_inventory import Bindings, asset
from tests.test_workflows import make_manifest, make_photo


@pytest.mark.parametrize("include_caption", [False, True])
@pytest.mark.parametrize("receipt_fault", [None, "missing_apply", "tampered_apply", "missing_rollback", "tampered_rollback"])
def test_default_automatic_gate_and_rollback_use_real_screenshot_adapter_protocol(tmp_path, monkeypatch, include_caption, receipt_fault):
    source_row = make_photo(1)
    if include_caption:
        source_row.proposed_caption = "Una playa tranquila."
        source_row.caption_state = "proposed"
    source_row.photos_local_identifier = source_row.uuid + "/L0/001"
    bindings = Bindings([asset(source_row.photos_local_identifier, source_row.date, subtype=8)])
    calls = []
    setters = []

    class NativePhoto:
        uuid = source_row.uuid
        id = source_row.photos_local_identifier
        title = ""
        date = source_row.date
        location = None

        def __init__(self):
            self._keywords = ["PERRO"]
            self._description = ""

        @property
        def description(self):
            return self._description

        @description.setter
        def description(self, value):
            setters.append(value)
            self._description = value

        @property
        def keywords(self):
            return list(self._keywords)

        @keywords.setter
        def keywords(self, values):
            setters.append(list(values))
            self._keywords = list(values)

    native_photo = NativePhoto()
    def photo_factory(identifier):
        assert identifier in {source_row.uuid, source_row.photos_local_identifier}
        calls.append(identifier)
        return native_photo

    monkeypatch.setattr(adapters.PhotoKitSelector, "_runtime", lambda self: bindings)
    with pytest.raises(adapters.AdapterError):
        adapters.PhotoKitSelector().revalidate(source_row.photos_local_identifier)
    monkeypatch.setattr(adapters, "_default_photos_library", lambda: object())
    monkeypatch.setattr(adapters, "_default_photo_factory", photo_factory)
    local_apply_dependencies = partial(workflows.ApplyDependencies, global_lock_path=tmp_path / "apply.lock")
    monkeypatch.setattr(workflows, "ApplyDependencies", local_apply_dependencies)
    root = tmp_path / "autonomous-campaigns"
    root.mkdir(mode=0o700)
    store = CampaignStore.create(root / CAMPAIGN, campaign_id=CAMPAIGN,
                                 config=SessionConfig(photo_count=1, inference_concurrency=1, include_caption=include_caption),
                                 records=[adapters.SelectedPhoto(source_row.photos_local_identifier, source_row.date)],
                                 limit=1, created_at=NOW)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    source = make_manifest(runs / "source", [source_row])
    store.authorize(*control(store), now=NOW)
    store.mark_examined(0)
    gate = AutomaticApplyGate(store)
    outcome = gate.process(0, source)
    assert outcome == "saved"
    assert native_photo.keywords == ["PERRO", "playa"]
    assert native_photo.description == (source_row.proposed_caption if include_caption else "")
    assert source_row.photos_local_identifier in calls and source_row.uuid in calls
    from photos_indexer.manifest import load_manifest
    reviewed = load_manifest(next(runs.glob("*-review-*/manifest.json")).parent)
    assert reviewed.photos[0].apply_state == "verified"
    assert reviewed.photos[0].errors == []
    assert store.status()["saved"] == 1
    reviewed_path = next(runs.glob("*-review-*/manifest.json"))
    link_path = reviewed_path.parent / "autonomy.json"
    link_bytes = link_path.read_bytes()
    link_path.write_text("{}")
    previous_calls = list(calls)
    from photos_indexer.service import review_manifest_v4
    unapplied = review_manifest_v4(source, {source_row.uuid: ["playa"]}, {})
    invalid_link = unapplied.parent / "autonomy.json"
    invalid_link.write_text("{}")
    invalid_link.chmod(0o600)
    for operation, operation_path in ((workflows.run_apply, unapplied), (workflows.run_rollback, reviewed_path)):
        rejected = operation(operation_path)
        assert rejected.error_codes == ("REVIEW_PROVENANCE_INVALID",)
        assert calls == previous_calls
        assert native_photo.keywords == ["PERRO", "playa"]
    link_path.write_bytes(link_bytes)
    rollback = workflows.run_rollback(reviewed_path)
    assert rollback.exit_code == 0
    assert rollback.error_codes == ()
    assert native_photo.keywords == ["PERRO"]
    assert native_photo.description == ""
    assert load_manifest(reviewed_path.parent).photos[0].rollback_state == "verified_removed"
    after_rollback_calls, after_rollback_setters = list(calls), list(setters)

    def forbidden_runner(*args, **kwargs):
        pytest.fail("load/replay must not access Photos, review or apply again")

    if receipt_fault is not None:
        payload = json.loads(reviewed_path.read_text())
        field = "mutation_digest" if receipt_fault.endswith("apply") else "rollback_digest"
        payload["photos"][0][field] = None if receipt_fault.startswith("missing") else "0" * 64
        reviewed_path.write_text(json.dumps(payload))
        with pytest.raises(CampaignStorageError):
            CampaignStore.load(store.path)
        with pytest.raises(CampaignStorageError):
            AutomaticApplyGate(store, bridge_factory=forbidden_runner, review_runner=forbidden_runner,
                               apply_runner=forbidden_runner).process(0, source)
    else:
        reopened = CampaignStore.load(store.path)
        assert reopened.status()["saved"] == 1
        assert AutomaticApplyGate(reopened, bridge_factory=forbidden_runner, review_runner=forbidden_runner,
                                  apply_runner=forbidden_runner).process(0, source) == "saved"
        assert workflows.run_apply(reviewed_path).error_codes == ("ROLLBACK_ALREADY_STARTED",)
    assert calls == after_rollback_calls
    assert setters == after_rollback_setters


@pytest.mark.parametrize("invalid", ["video", "identity", "date", "boolean_media"])
def test_autonomous_screenshot_revalidation_retains_identity_image_and_date_guards(invalid):
    from datetime import datetime
    current = asset("shot", datetime(2026, 9, 12), subtype=8)
    bindings = Bindings([current])
    if invalid == "video":
        current.mediaType = 2
    elif invalid == "identity":
        bindings.asset_for_local_id = lambda local_id: asset("other", current.creationDate, subtype=8)
    elif invalid == "date":
        current.creationDate = None
    else:
        current.mediaType = True
    selector = adapters.AutonomousPhotoKitSelector(bindings)
    with pytest.raises(adapters.AdapterError):
        selector.revalidate("shot")
