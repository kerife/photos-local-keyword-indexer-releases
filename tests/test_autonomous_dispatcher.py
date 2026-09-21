"""Production autonomous routing and bounded dispatch; no native Photos calls."""

import io
import json
import threading
import time
from datetime import datetime, timezone
from dataclasses import replace

import pytest

from photos_indexer.ipc import IPCProtocolError, _safe_service_event, parse_request_line, serve


def runtime_fixture(tmp_path, count=3, *, capacity=3, concurrency=2):
    from photos_indexer.autonomous_dispatcher import AutonomousCampaignRuntime
    from photos_indexer.adapters import SelectedPhoto, ScriptPhotoRecord
    from photos_indexer.queue_runtime import QueueRuntimeDependencies
    from tests.test_queue_runtime import _write_settings, _Bridge, _Vision, _Landmarks
    _write_settings(tmp_path / "settings.json", limit=capacity, concurrency=concurrency)
    selected = [SelectedPhoto(f"local-{i}", datetime(2026, 9, 12)) for i in range(count)]
    records = {photo.local_id: ScriptPhotoRecord(local_id=photo.local_id, uuid=f"00000000-0000-4000-8000-{i:012d}", title="", date=datetime(2026, 9, 12), existing_keywords=(), description="") for i, photo in enumerate(selected)}
    bridge = _Bridge(records)
    class Selector:
        inventories = 0
        exports = []
        def inventory(self):
            self.inventories += 1
            return iter([*selected, None])
        def export_local(self, local_id, destination):
            self.exports.append(local_id)
            return bridge.export(records[local_id].uuid, destination)
    selector = Selector()
    deps = QueueRuntimeDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge,
                                    vision_factory=_Vision, landmark_resolver_factory=_Landmarks,
                                    runs_root_factory=lambda: tmp_path / "runs",
                                    now_utc=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc))
    runtime = AutonomousCampaignRuntime(dependencies=deps)
    payload = dict(start_payload(), runs_root=str(tmp_path / "runs"), settings_path=str(tmp_path / "settings.json"))
    from photos_indexer.ipc import IPCRequest
    return runtime, IPCRequest("start", "autonomy_start", payload), selector, bridge


def wait_runtime(runtime):
    runtime._thread.join(20)
    assert not runtime._thread.is_alive()


def test_shared_preparation_exports_local_identity_and_cleans_workspace(tmp_path):
    runtime, request, selector, bridge = runtime_fixture(tmp_path)
    # Useful values already exist: production gate settles no-change without writes.
    bridge.records = {key: replace(value, existing_keywords=("gato", "perro"), description="Existing") for key, value in bridge.records.items()}
    events = []
    assert runtime.handle(request, events.append)
    wait_runtime(runtime)
    status = runtime.status()
    assert status["state"] == "completed"
    assert status["analyzed"] == status["examined"] == status["no_change"] == 3
    assert status["invalid_count"] == 1
    assert sorted(selector.exports) == ["local-0", "local-1", "local-2"]
    assert not list((tmp_path / "runs").glob("*/.exports-*"))
    activity = [event for event in events if event.get("type") == "autonomy_activity"]
    assert activity
    assert {event["state"] for event in activity}.issuperset({"preparing", "analyzing", "validating", "save_queued", "saving", "settled"})
    assert all(
        set(event) == {"type", "campaign_id", "revision", "position", "state", "photos_local_identifier"}
        for event in activity
    )


def test_bounded_601_records_progress_and_fixed_snapshot(tmp_path):
    runtime, request, selector, _ = runtime_fixture(tmp_path, 601, capacity=4, concurrency=2)
    lock = threading.Lock()
    first_pair = threading.Barrier(2)
    overlapped = threading.Event()
    seen = []
    active = peak = 0
    def failed_analysis(selected, *args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            seen.append(selected.local_id)
            is_first_pair = len(seen) <= 2
        try:
            if is_first_pair and first_pair.wait(timeout=5) == 0:
                overlapped.set()
        finally:
            with lock:
                active -= 1
        raise ValueError("ordinary unavailable photo")
    runtime._analyze = failed_analysis
    events = []
    runtime.handle(request, events.append)
    wait_runtime(runtime)
    status = runtime.status()
    assert status["state"] == "completed"
    assert status["examined"] == status["attention"] == 601
    assert status["in_flight"] == status["remaining"] == 0
    assert len(seen) == len(set(seen)) == 601
    assert overlapped.is_set()
    assert 1 < peak <= 2
    campaign_events = [event for event in events if event.get("type") == "autonomy_campaign"]
    assert max(event["in_flight"] for event in campaign_events) <= 4
    assert selector.inventories == 1
    assert runtime._store.snapshot.chunk_count == 4


def test_pause_during_handoff_revokes_activation_and_replay_is_passive(tmp_path):
    from photos_indexer.ipc import IPCRequest
    runtime, request, selector, _ = runtime_fixture(tmp_path)
    entered, finish, generation = threading.Event(), threading.Event(), threading.Event()
    def drain():
        entered.set()
        assert finish.wait(5)
    events = []
    thread = threading.Thread(target=lambda: runtime.handle(request, events.append, generation=generation, drain=drain))
    thread.start()
    assert entered.wait(5)
    generation.set()
    runtime.revoke()
    finish.set()
    thread.join(5)
    assert runtime.status()["state"] == "paused"
    assert selector.inventories == 0
    assert not runtime.handle(request, events.append)
    assert selector.inventories == 0
    recovered = type(runtime)(dependencies=runtime.dependencies)
    assert recovered.status()["reason"] == "recovered"
    assert selector.inventories == 0
    with pytest.raises(ValueError):
        recovered.handle(IPCRequest("resume", "autonomy_resume", dict(campaign_id="campaign-1", decision_id="resume-1")), events.append)


def test_pause_during_analysis_reuses_durable_source_on_explicit_resume(tmp_path):
    from photos_indexer.ipc import IPCRequest
    from tests.test_queue_runtime import _Vision
    runtime, request, selector, bridge = runtime_fixture(tmp_path, 1, capacity=1, concurrency=1)
    bridge.records = {key: replace(value, existing_keywords=("gato", "perro"), description="Existing") for key, value in bridge.records.items()}
    entered, finish = threading.Event(), threading.Event()
    class Vision(_Vision):
        calls = 0
        def analyze(self, *args, **kwargs):
            type(self).calls += 1
            entered.set()
            assert finish.wait(5)
            return super().analyze(*args, **kwargs)
    runtime.dependencies = replace(runtime.dependencies, vision_factory=Vision)
    events = []
    runtime.handle(request, events.append)
    assert entered.wait(5)
    runtime.revoke()
    finish.set()
    wait_runtime(runtime)
    assert runtime.status()["state"] == "paused"
    assert runtime.status()["analyzed"] == 1
    assert runtime.status()["no_change"] == 0
    runtime.handle(IPCRequest("resume", "autonomy_resume", dict(campaign_id="campaign-1", decision_id="resume-1")), events.append)
    wait_runtime(runtime)
    assert runtime.status()["no_change"] == 1
    assert selector.inventories == Vision.calls == 1


@pytest.mark.parametrize("count, cap, expected", [(0, None, 0), (10, 3, 3)])
def test_empty_inventory_and_attempt_cap(tmp_path, count, cap, expected):
    runtime, request, selector, _ = runtime_fixture(tmp_path, count)
    request.payload["limit"] = cap
    def unavailable(*args):
        raise ValueError("not local")
    runtime._analyze = unavailable
    runtime.handle(request, lambda event: None)
    wait_runtime(runtime)
    assert runtime.status()["state"] == "completed"
    assert runtime.status()["total"] == runtime.status()["examined"] == expected
    assert runtime.status()["attention"] == expected
    assert runtime.status()["invalid_count"] == 1


@pytest.mark.parametrize("failure, reason", [("permission", "permission"), ("local", "none")])
def test_source_preparation_error_codes_pause_only_permissions(tmp_path, failure, reason):
    from photos_indexer.adapters import PhotosAccessError, LocalPhotoUnavailableError
    runtime, request, selector, _ = runtime_fixture(tmp_path, 5, capacity=1, concurrency=1)
    def fail_export(*args):
        raise PhotosAccessError() if failure == "permission" else LocalPhotoUnavailableError()
    selector.export_local = fail_export
    runtime.handle(request, lambda event: None)
    wait_runtime(runtime)
    status = runtime.status()
    assert status["reason"] == reason
    assert status["state"] == ("paused" if failure == "permission" else "completed")
    assert status["examined"] == status["attention"] == (1 if failure == "permission" else 5)
    assert status["analyzed"] == status["in_flight"] == 0


def test_recovery_status_never_constructs_external_clients(tmp_path):
    runtime, request, selector, bridge = runtime_fixture(tmp_path, 1)
    bridge.records = {key: replace(value, existing_keywords=("gato", "perro"), description="Existing") for key, value in bridge.records.items()}
    runtime.handle(request, lambda event: None)
    wait_runtime(runtime)
    def forbidden():
        pytest.fail("status instantiated an external client")
    dependencies = replace(runtime.dependencies, selector_factory=forbidden, bridge_factory=forbidden, vision_factory=forbidden)
    recovered = type(runtime)(dependencies=dependencies)
    status = recovered.status()
    assert status["state"] == "paused"
    assert status["reason"] == "recovered"
    assert status["no_change"] == 1
    assert not recovered.handle(request, lambda event: None)


def test_server_handoff_drains_admitted_saves_and_analysis_preserves_manual_session(tmp_path, monkeypatch):
    from photos_indexer.ipc import IPCRequest, _WorkerServer
    runtime, request, selector, bridge = runtime_fixture(tmp_path, 1)
    bridge.records = {key: replace(value, existing_keywords=("gato", "perro"), description="Existing") for key, value in bridge.records.items()}
    monkeypatch.setattr("photos_indexer.ipc.validate_app_runs_root", lambda path, **kwargs: tmp_path / "runs")
    entered = [threading.Event(), threading.Event()]
    finish = [threading.Event(), threading.Event()]
    analyzed = threading.Event()
    paused = threading.Event()
    order = []
    class Manual:
        def handle(self, req, emit):
            index = int(req.payload["item_id"])
            entered[index].set()
            assert finish[index].wait(5)
            order.append(f"save-{index}")
        def pause_for_handoff(self):
            paused.set()
        def drain_for_handoff(self):
            self.pause_for_handoff()
            assert analyzed.wait(5)
            order.append("analysis-drained")
    manual = Manual()
    original_inventory = selector.inventory
    def inventory():
        assert set(order) == {"save-0", "save-1", "analysis-drained"}
        return original_inventory()
    selector.inventory = inventory
    output = io.StringIO()
    server = _WorkerServer(output, scan_handler=None, apply_handler=None, rollback_handler=None,
                           review_handler=None, preflight_handler=None, queue_handler=manual.handle, autonomy_runtime=runtime)
    server._queue_session_id = "retained-manual"
    for index in range(2):
        server.start(IPCRequest(f"save-{index}", "queue_persist", dict(session_id="retained-manual", item_id=str(index), revision=0, decision_id=f"save-{index}")))
        assert entered[index].wait(5)
    server.start(request)
    assert paused.wait(5)
    server.start(IPCRequest("rejected", "queue_persist", dict(session_id="retained-manual", item_id="0", revision=0, decision_id="new-save")))
    assert selector.inventories == 0
    finish[0].set()
    finish[1].set()
    analyzed.set()
    deadline = time.monotonic() + 5
    while runtime._thread is None and time.monotonic() < deadline:
        time.sleep(.001)
    wait_runtime(runtime)
    server.join()
    assert server._queue_session_id == "retained-manual"
    assert server._autonomy_owner is None
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert dict(id="rejected", event="error", code="AUTONOMY_ACTIVE") in events
    assert sum(event["event"] == "completed" and event["id"] == "start" for event in events) == 1


def test_server_pause_during_manual_handoff_releases_autonomy_without_waiting_for_manual_completion(tmp_path, monkeypatch):
    from photos_indexer.ipc import IPCRequest, _WorkerServer
    runtime, request, selector, _ = runtime_fixture(tmp_path, 1)
    monkeypatch.setattr("photos_indexer.ipc.validate_app_runs_root", lambda path, **kwargs: tmp_path / "runs")
    entered, finish, paused = threading.Event(), threading.Event(), threading.Event()

    class Manual:
        def handle(self, req, emit):
            entered.set()
            assert finish.wait(5)

        def pause_for_handoff(self):
            paused.set()

        def drain_for_handoff(self):
            self.pause_for_handoff()

    output = io.StringIO()
    server = _WorkerServer(
        output,
        scan_handler=None,
        apply_handler=None,
        rollback_handler=None,
        review_handler=None,
        preflight_handler=None,
        queue_handler=Manual().handle,
        autonomy_runtime=runtime,
    )
    server._queue_session_id = "retained-manual"
    server.start(IPCRequest("manual", "queue_persist", dict(
        session_id="retained-manual", item_id="0", revision=0, decision_id="manual-save",
    )))
    assert entered.wait(5)
    server.start(request)
    assert paused.wait(5)
    server.start(IPCRequest("pause", "autonomy_pause", dict(
        campaign_id="campaign-1", decision_id="pause-1",
    )))
    deadline = time.monotonic() + 2
    while runtime.status()["state"] != "paused" and time.monotonic() < deadline:
        time.sleep(.001)
    assert runtime.status()["state"] == "paused"
    assert selector.inventories == 0
    assert server._autonomy_owner is None
    finish.set()
    server.join()


def test_server_pause_during_coordinator_drain_releases_autonomy_without_waiting(tmp_path, monkeypatch):
    """A pause must not be held hostage by work already admitted to manual review."""
    from photos_indexer.ipc import IPCRequest, _WorkerServer
    runtime, request, selector, _ = runtime_fixture(tmp_path, 1)
    monkeypatch.setattr("photos_indexer.ipc.validate_app_runs_root", lambda path, **kwargs: tmp_path / "runs")
    draining, finish = threading.Event(), threading.Event()

    class Manual:
        def handle(self, req, emit):
            return None

        def pause_for_handoff(self):
            return None

        def drain_for_handoff(self):
            draining.set()
            assert finish.wait(5)

    output = io.StringIO()
    server = _WorkerServer(
        output,
        scan_handler=None,
        apply_handler=None,
        rollback_handler=None,
        review_handler=None,
        preflight_handler=None,
        queue_handler=Manual().handle,
        autonomy_runtime=runtime,
    )
    try:
        server._queue_session_id = "retained-manual"
        server.start(request)
        assert draining.wait(5)
        server.start(IPCRequest("pause", "autonomy_pause", dict(
            campaign_id="campaign-1", decision_id="pause-1",
        )))
        deadline = time.monotonic() + 2
        while server._autonomy_owner is not None and time.monotonic() < deadline:
            time.sleep(.001)
        assert server._autonomy_owner is None
        assert selector.inventories == 0
    finally:
        finish.set()
        server.join()


def test_photos_lane_serializes_and_prioritizes_waiting_write():
    from photos_indexer.autonomous_dispatcher import _PhotosLane
    lane = _PhotosLane()
    entered, release, writer_waiting = threading.Event(), threading.Event(), threading.Event()
    order = []
    def first_read():
        with lane.operation():
            entered.set()
            assert release.wait(5)
            order.append("first")
    def writer():
        writer_waiting.set()
        with lane.operation(write=True):
            order.append("write")
    def reader():
        with lane.operation():
            order.append("read")
    first = threading.Thread(target=first_read)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=writer)
    second.start()
    assert writer_waiting.wait(5)
    with lane.condition:
        assert lane.writers == 1
    third = threading.Thread(target=reader)
    third.start()
    release.set()
    for thread in (first, second, third):
        thread.join(5)
    assert order == ["first", "write", "read"]


def test_storage_failure_closes_admission_without_applying(tmp_path, monkeypatch):
    from photos_indexer.autonomous_runtime import CampaignStorageError
    runtime, request, selector, _ = runtime_fixture(tmp_path, 5, capacity=1, concurrency=1)
    calls = []
    def unavailable(*args, **kwargs):
        raise CampaignStorageError("source evidence storage unavailable")
    monkeypatch.setattr("photos_indexer.autonomous_dispatcher._persist_scan", unavailable)
    runtime.dependencies = replace(runtime.dependencies, apply_runner=lambda path: calls.append(path))
    runtime.handle(request, lambda event: None)
    wait_runtime(runtime)
    assert runtime.status()["state"] == "paused"
    assert runtime.status()["reason"] == "storage"
    assert runtime.status()["examined"] == 1
    assert runtime.status()["in_flight"] == 0
    assert calls == []
    assert not list((tmp_path / "runs").glob("*/.exports-*"))


def test_server_eof_during_handoff_prevents_late_inventory(tmp_path, monkeypatch):
    from photos_indexer.ipc import _WorkerServer
    runtime, request, selector, _ = runtime_fixture(tmp_path, 5)
    monkeypatch.setattr("photos_indexer.ipc.validate_app_runs_root", lambda path, **kwargs: tmp_path / "runs")
    entered, finish = threading.Event(), threading.Event()
    class Manual:
        def handle(self, req, emit):
            pass
        def pause_for_handoff(self):
            entered.set()
            assert finish.wait(5)
        def drain_for_handoff(self):
            pass
    output = io.StringIO()
    manual = Manual()
    server = _WorkerServer(output, scan_handler=None, apply_handler=None, rollback_handler=None,
                           review_handler=None, preflight_handler=None, queue_handler=manual.handle, autonomy_runtime=runtime)
    server.start(request)
    assert entered.wait(5)
    shutdown = threading.Thread(target=server.shutdown)
    shutdown.start()
    deadline = time.monotonic() + 5
    while not server._closing and time.monotonic() < deadline:
        time.sleep(.001)
    assert server._closing
    finish.set()
    shutdown.join(5)
    assert not shutdown.is_alive()
    assert selector.inventories == 0
    assert runtime.status()["state"] == "paused"


def test_pause_after_write_admission_finishes_receipt_and_never_replays(tmp_path):
    from photos_indexer.adapters import SelectedPhoto
    from photos_indexer.ipc import IPCRequest
    from photos_indexer.workflows import ApplyDependencies, run_apply
    from tests.test_workflows import make_photo, make_manifest, StatefulBridge, RevalidatingSelector
    runtime, request, selector, _ = runtime_fixture(tmp_path, 1, capacity=1, concurrency=1)
    photo = make_photo(1)
    selector.inventory = lambda: iter([SelectedPhoto(photo.photos_local_identifier, photo.date)])
    bridge = StatefulBridge({"local-1": ["PERRO"]})
    entered, finish = threading.Event(), threading.Event()
    calls = []
    deps = ApplyDependencies(selector_factory=RevalidatingSelector, bridge_factory=lambda: bridge,
                             global_lock_path=tmp_path / "apply.lock")
    def apply(path):
        calls.append(path)
        assert (runtime._store._position_path(0) / "decision.json").exists()
        entered.set()
        assert finish.wait(5)
        return run_apply(path, dependencies=deps)
    runtime.dependencies = replace(runtime.dependencies, bridge_factory=lambda: bridge, apply_runner=apply)
    runtime._analyze = lambda *args: make_manifest(tmp_path / "runs" / "source", [photo])
    events = []
    runtime.handle(request, events.append)
    assert entered.wait(5)
    pause_request = IPCRequest("pause", "autonomy_pause", dict(campaign_id="campaign-1", decision_id="pause-1"))
    pause = threading.Thread(target=lambda: runtime.handle(pause_request, events.append))
    pause.start()
    deadline = time.monotonic() + 5
    while runtime._store.status()["state"] != "pausing" and time.monotonic() < deadline:
        time.sleep(.001)
    assert pause.is_alive()
    finish.set()
    pause.join(5)
    assert not pause.is_alive()
    assert runtime.status()["saved"] == 1
    assert len(bridge.replace_calls) == len(calls) == 1
    assert not runtime.handle(request, events.append)
    assert len(calls) == 1
    recovered = type(runtime)(dependencies=runtime.dependencies)
    assert recovered.status()["saved"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["permission", "storage"])
def test_last_position_gate_failure_remains_paused(tmp_path, failure):
    from photos_indexer.adapters import PhotosAccessError
    from photos_indexer.workflows import WorkflowResult
    from tests.test_workflows import make_photo, make_manifest
    runtime, request, _, bridge = runtime_fixture(tmp_path, 1, capacity=1, concurrency=1)
    photo = make_photo(1)
    photo.photos_local_identifier = "local-0"
    photo.uuid = bridge.records["local-0"].uuid
    runtime._analyze = lambda *args: make_manifest(tmp_path / "runs" / "source", [photo])
    if failure == "permission":
        def denied(local_id):
            raise PhotosAccessError()
        bridge.read = denied
    else:
        runtime.dependencies = replace(runtime.dependencies, apply_runner=lambda path: WorkflowResult(
            exit_code=1, manifest_path=path, error_codes=("LOCK_OR_MANIFEST_FAILED",)))
    events = []
    runtime.handle(request, events.append)
    wait_runtime(runtime)
    assert runtime.status()["remaining"] == 0
    assert runtime.status()["state"] == "paused"
    assert runtime.status()["reason"] == failure


@pytest.mark.parametrize("failure", ["permission", "storage"])
def test_later_accepted_settlements_cannot_publish_completed_after_global_failure(tmp_path, failure):
    from tests.test_autonomous_campaign import create_store, control, NOW
    store = create_store(tmp_path, count=2)
    store.authorize(*control(store), now=NOW)
    store.mark_examined(0)
    store.mark_examined(1)
    store.fail_closed(failure)
    store.settle(0, "attention", reason="failed")
    assert store.status()["state"] == "pausing"
    store.settle(1, "attention", reason="failed")
    assert store.status()["remaining"] == 0
    assert store.status()["state"] == "paused"
    assert store.status()["reason"] == failure


@pytest.mark.parametrize("prior_campaign", [False, True])
@pytest.mark.parametrize("stage", ["provisional", "intake"])
def test_interrupted_intake_is_recovered_without_latest_publication(tmp_path, monkeypatch, prior_campaign, stage):
    import photos_indexer.autonomous_dispatcher as dispatcher
    runtime, request, selector, _ = runtime_fixture(tmp_path, 0)
    if prior_campaign:
        runtime.handle(request, lambda event: None)
        wait_runtime(runtime)
        request = replace(request, payload=dict(request.payload, campaign_id="campaign-2", decision_id="decision-2"))
    def interrupted():
        raise OSError("interrupted provisional publication")
    with monkeypatch.context() as patch:
        if stage == "provisional":
            patch.setattr(runtime, "_save_provisional", interrupted)
        else:
            original_write = dispatcher._write
            def interrupted_intake(path, *args, **kwargs):
                if path.parent.name == "intakes" and path.stem == request.payload["decision_id"]:
                    raise OSError("interrupted replay index publication")
                return original_write(path, *args, **kwargs)
            patch.setattr(dispatcher, "_write", interrupted_intake)
        with pytest.raises(OSError):
            runtime.handle(request, lambda event: None)
    inventories = selector.inventories
    recovered = type(runtime)(dependencies=runtime.dependencies)
    status = recovered.status()
    assert status is not None
    assert status["campaign_id"] == request.payload["campaign_id"]
    assert status["state"] == "paused" and status["reason"] == "recovered"
    assert not recovered.handle(request, lambda event: None)
    assert selector.inventories == inventories


@pytest.mark.parametrize("through_server", [False, True])
def test_old_pause_replay_and_conflict_do_not_revoke_new_resume(tmp_path, through_server):
    from photos_indexer.ipc import IPCRequest, _WorkerServer
    from tests.test_queue_runtime import _Vision
    runtime, request, _, bridge = runtime_fixture(tmp_path, 2, capacity=1, concurrency=1)
    bridge.records = {key: replace(value, existing_keywords=("gato", "perro"), description="Existing") for key, value in bridge.records.items()}
    entered, finish = threading.Event(), threading.Event()
    class Vision(_Vision):
        def analyze(self, *args, **kwargs):
            entered.set()
            assert finish.wait(5)
            return super().analyze(*args, **kwargs)
    runtime.dependencies = replace(runtime.dependencies, vision_factory=Vision)
    runtime.handle(request, lambda event: None)
    assert entered.wait(5)
    pause_request = IPCRequest("pause-a", "autonomy_pause", dict(campaign_id="campaign-1", decision_id="pause-a"))
    pause = threading.Thread(target=lambda: runtime.handle(pause_request, lambda event: None))
    pause.start()
    deadline = time.monotonic() + 5
    while not runtime._generation.is_set() and time.monotonic() < deadline:
        time.sleep(.001)
    finish.set()
    pause.join(5)
    entered.clear()
    finish.clear()
    runtime.handle(IPCRequest("resume-b", "autonomy_resume", dict(campaign_id="campaign-1", decision_id="resume-b")), lambda event: None)
    assert entered.wait(5)
    generation = runtime._generation
    if through_server:
        output = io.StringIO()
        server = _WorkerServer(output, scan_handler=None, apply_handler=None, rollback_handler=None,
                               review_handler=None, preflight_handler=None, autonomy_runtime=runtime)
        server._autonomy_owner = "campaign-1"
        server._autonomy_generation = generation
        server.start(pause_request)
        with server._state_lock:
            controls = tuple(server._autonomy_threads)
        for control in controls:
            control.join(2)
    else:
        assert not runtime.handle(pause_request, lambda event: None)
    try:
        assert not generation.is_set()
        assert runtime.status()["state"] == "running"
        assert runtime._thread.is_alive()
        conflict = IPCRequest("conflict", "autonomy_pause", dict(campaign_id="campaign-1", decision_id="resume-b"))
        if through_server:
            server.start(conflict)
        else:
            with pytest.raises(ValueError):
                runtime.handle(conflict, lambda event: None)
        assert not generation.is_set()
    finally:
        finish.set()
        wait_runtime(runtime)
        if through_server:
            server.join()


@pytest.mark.parametrize("through_server", [False, True])
def test_pause_intake_storage_failure_revokes_before_later_write_admission(tmp_path, monkeypatch, through_server):
    from photos_indexer.autonomous_runtime import CampaignStorageError
    from photos_indexer.ipc import IPCRequest, _WorkerServer
    from tests.test_queue_runtime import _Vision
    runtime, request, _, _ = runtime_fixture(tmp_path, 2, capacity=1, concurrency=1)
    entered, finish = threading.Event(), threading.Event()
    writes = []
    class Vision(_Vision):
        def analyze(self, *args, **kwargs):
            entered.set()
            assert finish.wait(5)
            return super().analyze(*args, **kwargs)
    runtime.dependencies = replace(runtime.dependencies, vision_factory=Vision,
                                   apply_runner=lambda path: writes.append(path))
    runtime.handle(request, lambda event: None)
    assert entered.wait(5)
    generation = runtime._generation
    def fail_intake(req):
        raise CampaignStorageError("pause intake publication failed")
    monkeypatch.setattr(runtime, "_intake", fail_intake)
    pause = IPCRequest("pause-failed", "autonomy_pause", dict(campaign_id="campaign-1", decision_id="pause-failed"))
    if through_server:
        output = io.StringIO()
        server = _WorkerServer(output, scan_handler=None, apply_handler=None, rollback_handler=None,
                               review_handler=None, preflight_handler=None, autonomy_runtime=runtime)
        server._autonomy_owner = "campaign-1"
        # Exercise the server-owned pending generation as well as the runtime's
        # existing generation: either ownership phase must close on disk failure.
        server._autonomy_generation = threading.Event()
        server.start(pause)
    else:
        with pytest.raises(CampaignStorageError):
            runtime.handle(pause, lambda event: None)
    try:
        assert generation.is_set()
        assert runtime._store._authorization is None
        assert runtime.status()["reason"] == "storage"
        if through_server:
            assert server._autonomy_generation.is_set()
    finally:
        finish.set()
        wait_runtime(runtime)
        if through_server:
            server.join()
    assert runtime.status()["state"] == "paused"
    assert runtime.status()["reason"] == "storage"
    assert runtime.status()["examined"] == 1
    assert writes == []
    assert not list(runtime._store.path.glob("positions/*/decision.json"))


def test_interrupted_initial_checkpoint_recovers_paused_and_allows_new_campaign(tmp_path, monkeypatch):
    import photos_indexer.autonomous_runtime as core
    runtime, request, selector, _ = runtime_fixture(tmp_path, 1)
    original_write = core._write
    def interrupted(path, *args, **kwargs):
        if path.name == "checkpoint.json":
            raise OSError("interrupted initial checkpoint publication")
        return original_write(path, *args, **kwargs)
    events = []
    with monkeypatch.context() as patch:
        patch.setattr(core, "_write", interrupted)
        with pytest.raises(core.CampaignStorageError):
            runtime.handle(request, events.append)
    old_campaign = runtime._root / "campaign-1"
    old_metadata = (old_campaign / "campaign.json").read_bytes()
    assert not (old_campaign / "checkpoint.json").exists()
    assert selector.inventories == 1
    recovered = type(runtime)(dependencies=runtime.dependencies)
    status = recovered.status()
    assert status["state"] == "paused" and status["reason"] == "recovered"
    assert status["examined"] == status["in_flight"] == status["saved"] == 0
    assert status["revision"] > max(event["revision"] for event in events)
    assert selector.inventories == 1
    assert not list(old_campaign.glob("positions/*/decision.json"))
    assert not recovered.handle(request, lambda event: None)
    assert selector.inventories == 1
    new_request = replace(request, payload=dict(request.payload, campaign_id="campaign-2", decision_id="decision-2"))
    recovered._analyze = lambda *args: (_ for _ in ()).throw(ValueError("ordinary unavailable photo"))
    recovered.handle(new_request, lambda event: None)
    wait_runtime(recovered)
    assert selector.inventories == 2
    assert recovered.status()["campaign_id"] == "campaign-2"
    assert (old_campaign / "campaign.json").read_bytes() == old_metadata


def test_missing_checkpoint_after_authorization_is_not_repaired(tmp_path):
    from photos_indexer.autonomous_runtime import CampaignStore, CampaignStorageError
    from tests.test_autonomous_campaign import create_store, control, NOW
    store = create_store(tmp_path, count=2)
    store.authorize(*control(store), now=NOW)
    store.mark_examined(0)
    (store.path / "checkpoint.json").unlink()
    with pytest.raises(CampaignStorageError):
        CampaignStore.load(store.path)
    assert not (store.path / "checkpoint.json").exists()


@pytest.mark.parametrize("native_exporter", [False, True])
def test_export_disk_full_pauses_storage_after_first_attempt(tmp_path, monkeypatch, native_exporter):
    import errno
    from photos_indexer import adapters
    from tests.test_autonomous_inventory import Bindings, asset
    runtime, request, selector, _ = runtime_fixture(tmp_path, 3, capacity=1, concurrency=1)
    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "local export storage exhausted")
    if native_exporter:
        bindings = Bindings([asset(f"local-{index}", datetime(2026, 9, 12)) for index in range(3)])
        bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff\xe0test\xff\xd9"
        selector.export_local = adapters.PhotoKitSelector(bindings).export_local
        original_open = adapters.os.open
        def failing_output_open(path, *args, **kwargs):
            return disk_full() if path == "local.jpg" else original_open(path, *args, **kwargs)
        monkeypatch.setattr(adapters.os, "open", failing_output_open)
    else:
        selector.export_local = disk_full
    writes = []
    runtime.dependencies = replace(runtime.dependencies, apply_runner=lambda path: writes.append(path))
    runtime.handle(request, lambda event: None)
    wait_runtime(runtime)
    assert runtime.status()["state"] == "paused"
    assert runtime.status()["reason"] == "storage"
    assert runtime.status()["examined"] == 1
    assert runtime.status()["analyzed"] == runtime.status()["in_flight"] == 0
    assert writes == []
    assert not list((tmp_path / "runs").glob("*/.exports-*"))


def start_payload():
    return dict(campaign_id="campaign-1", decision_id="decision-1",
                runs_root="/private/support/runs", settings_path="/private/support/settings.json", limit=None)


def test_wire_requires_explicit_null_and_exact_fields():
    payload = start_payload()
    assert parse_request_line(json.dumps(dict(id="req", command="autonomy_start", payload=payload))).payload == payload
    for bad in ({k: v for k, v in payload.items() if k != "limit"},
                dict(payload, limit=True), dict(payload, limit=0), dict(payload, limit=2**31),
                dict(payload, caption="private"), dict(payload, campaign_id="../escape")):
        with pytest.raises(IPCProtocolError):
            parse_request_line(json.dumps(dict(id="req", command="autonomy_start", payload=bad)))


def test_campaign_event_exact_projection():
    event = dict(type="autonomy_campaign", campaign_id="campaign-1", revision=0, state="paused", reason="recovered",
                 **{k: 0 for k in ("total", "examined", "analyzed", "saved", "no_change", "attention", "remaining", "in_flight", "invalid_count")})
    assert _safe_service_event(event)["event"] == "autonomy_campaign"
    for bad in (dict(event, photo="private"), dict(event, total=True), dict(event, reason="unknown"),
                {k: v for k, v in event.items() if k != "invalid_count"}):
        with pytest.raises(IPCProtocolError):
            _safe_service_event(bad)


def test_default_serve_routes_status_without_photos(tmp_path, monkeypatch):
    import photos_indexer.service as service
    import photos_indexer.queue_runtime as queue_runtime
    monkeypatch.setattr(service, "app_runs_root", lambda: tmp_path / "runs")
    monkeypatch.setattr(queue_runtime, "app_runs_root", lambda: tmp_path / "runs")
    output = io.StringIO()
    serve(io.StringIO('{"id":"status","command":"autonomy_status","payload":{}}\n'), output)
    events = [json.loads(line) for line in output.getvalue().splitlines()]
    assert events == [dict(id="status", event="completed", exit_code=0, next_action="none")]


def test_status_recovery_does_not_enumerate_position_evidence(tmp_path, monkeypatch):
    """A paused/completed campaign must not hold the UI behind a full replay."""
    import photos_indexer.autonomous_runtime as autonomous_runtime

    runtime, request, _, _ = runtime_fixture(tmp_path)
    events = []
    assert runtime.handle(request, events.append)
    wait_runtime(runtime)

    real_scandir = autonomous_runtime.os.scandir

    def no_position_replay(path):
        if str(path).endswith("/positions"):
            raise AssertionError("status recovery replayed every campaign position")
        return real_scandir(path)

    monkeypatch.setattr(autonomous_runtime.os, "scandir", no_position_replay)
    recovered = type(runtime)(dependencies=runtime.dependencies)

    status = recovered.status()

    assert status["state"] == "paused"
    assert status["reason"] == "recovered"
    assert status["total"] == 3
