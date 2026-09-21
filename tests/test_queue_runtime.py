from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from photos_indexer.adapters import (
    PhotoSelection,
    PhotoScriptPermissionError,
    ScriptPhotoRecord,
    SelectedPhoto,
    TemporaryExportWorkspace,
)
from photos_indexer.ipc import IPCRequest, QueueDecisionInvalidError
from photos_indexer.manifest import compute_mutation_digest, load_manifest, write_manifest
from photos_indexer.models import VisionResult
from photos_indexer.queue_runtime import ContinuousQueueRuntime, QueueRuntimeDependencies
from photos_indexer.queue_session import QueueItem, QueueSession, SessionConfig, load_session, write_session
from photos_indexer.workflows import WorkflowResult


class _Selector:
    def __init__(self, photos: list[SelectedPhoto]) -> None:
        self.photos = photos

    def select(self, *, limit: int, randomize: bool = False) -> PhotoSelection:
        del randomize
        selected = tuple(self.photos[:limit])
        return PhotoSelection(selected, limit, len(selected), 0, "authorized", "random")


class _Bridge:
    def __init__(self, records: dict[str, ScriptPhotoRecord]) -> None:
        self.records = records

    def read(self, local_id: str) -> ScriptPhotoRecord:
        return self.records[local_id]

    def export(self, photo_uuid: str, destination: Path) -> Path:
        path = destination / "photo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nqueue")
        return path


class _Vision:
    def check_model(self, model: str) -> str:
        return "0.32.1"

    def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
        del model, image, kwargs
        return VisionResult(
            keywords=("gato", "perro"),
            caption="Un gato y un perro descansan juntos.",
            contains_people=False,
            contains_text=False,
            confidence=0.91,
        )


class _RecordingVision(_Vision):
    def __init__(self, calls: list[tuple[str, dict[str, object]]], checked: list[str]) -> None:
        self.calls = calls
        self.checked = checked

    def check_model(self, model: str) -> str:
        self.checked.append(model)
        return super().check_model(model)

    def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
        self.calls.append((model, kwargs))
        return super().analyze(model, image, **kwargs)


class _BlockingRecordingVision(_RecordingVision):
    def __init__(
        self,
        calls: list[tuple[str, dict[str, object]]],
        checked: list[str],
        started: threading.Event,
        release: threading.Event,
    ) -> None:
        super().__init__(calls, checked)
        self.started = started
        self.release = release

    def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
        if not self.calls:
            self.started.set()
            assert self.release.wait(timeout=5)
        return super().analyze(model, image, **kwargs)


class _Landmarks:
    def resolve(self, location: object) -> None:
        return None


class _RecordingPlaces:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls

    def nearby(self, location: object, *, cancel_requested: object) -> tuple[str, ...]:
        del cancel_requested
        self.calls.append(location)
        return ()


def _write_settings(
    path: Path,
    *,
    limit: int = 2,
    concurrency: int = 2,
    model: str = "qwen3-vl:4b",
    include_caption: bool = True,
    apple_maps: bool = False,
) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps({
        "limit": limit,
        "modelPolicy": "single",
        "singleModel": model,
        "fastModel": "qwen3-vl:4b",
        "detailedModel": "qwen3-vl:4b",
        "appleMaps": apple_maps,
        "includeCaption": include_caption,
        "randomSelection": True,
        "autoAnalyze": True,
        "analysisConcurrency": concurrency,
        "version": 2,
    }), encoding="utf-8")
    path.chmod(0o600)


def _write_rescan(
    root: Path,
    *,
    session_id: str,
    item_id: str,
    revision: int,
    decision_id: str,
    places: bool = False,
) -> None:
    path = root / session_id / "rescans" / f"{decision_id}.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps({
        "session_id": session_id,
        "item_id": item_id,
        "revision": revision,
        "decision_id": decision_id,
        "model": "qwen3-vl:8b",
        "profile": "free_local",
        "layers": {
            "places": places,
            "documents_text": True,
            "people_accessories": True,
            "semantic_normalization": True,
        },
        "additional_information": "Hay dos especies distintas visibles.",
        "analysis_prompt": "Distingue cada animal por sus rasgos visibles.",
        "reset_prompt": False,
        "reset_edits": False,
    }), encoding="utf-8")
    path.chmod(0o600)


def _write_decision(
    root: Path,
    *,
    session_id: str,
    item_id: str,
    revision: int,
    decision_id: str,
    keywords: list[str] | None = None,
    caption: str | None = "Un gato y un perro descansan juntos.",
) -> None:
    path = root / session_id / "decisions" / f"{decision_id}.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_text(json.dumps({
        "session_id": session_id,
        "item_id": item_id,
        "revision": revision,
        "decision_id": decision_id,
        "approved_keywords": keywords or ["gato", "perro", "mascotas"],
        "approved_caption": caption,
    }), encoding="utf-8")
    path.chmod(0o600)


def _runtime(tmp_path: Path, *, apply_calls: list[Path]) -> tuple[ContinuousQueueRuntime, Path, Path]:
    runs_root = tmp_path / "runs"
    runs_root.mkdir(mode=0o700)
    settings = tmp_path / "settings.json"
    _write_settings(settings)
    photos = [
        SelectedPhoto("local-clean-1", datetime(2026, 1, 1)),
        SelectedPhoto("local-tagged", datetime(2026, 1, 2)),
        SelectedPhoto("local-clean-2", datetime(2026, 1, 3)),
    ]
    records = {
        "local-clean-1": ScriptPhotoRecord(
            uuid=str(uuid.uuid4()), local_id="local-clean-1", title="", date=datetime(2026, 1, 1),
            existing_keywords=(), description="",
        ),
        "local-tagged": ScriptPhotoRecord(
            uuid=str(uuid.uuid4()), local_id="local-tagged", title="", date=datetime(2026, 1, 2),
            existing_keywords=("existente",), description="",
        ),
        "local-clean-2": ScriptPhotoRecord(
            uuid=str(uuid.uuid4()), local_id="local-clean-2", title="", date=datetime(2026, 1, 3),
            existing_keywords=(), description="   ",
        ),
    }

    def apply_runner(path: Path) -> WorkflowResult:
        apply_calls.append(path)
        manifest = load_manifest(path.parent)
        for photo in manifest.photos:
            photo.apply_state = "verified"
            photo.applied_keywords = list(photo.proposed_keywords)
            if photo.proposed_caption is not None:
                photo.applied_caption = photo.proposed_caption
                photo.caption_state = "verified"
            photo.mutation_digest = compute_mutation_digest(manifest, photo)
        write_manifest(path.parent, manifest)
        return WorkflowResult(exit_code=0, manifest_path=path, manifest=manifest)

    runtime = ContinuousQueueRuntime(dependencies=QueueRuntimeDependencies(
        selector_factory=lambda: _Selector(photos),
        bridge_factory=lambda: _Bridge(records),
        vision_factory=_Vision,
        landmark_resolver_factory=_Landmarks,
        apply_runner=apply_runner,
    ))
    return runtime, runs_root, settings


def test_queue_runtime_filters_existing_metadata_and_streams_each_ready_item(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []

    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)

    ready = [event for event in events if event.get("type") == "queue_item" and event.get("state") == "ready"]
    assert len(ready) == 2
    assert all(Path(str(event["manifest"])).is_file() for event in ready)
    assert all(load_manifest(Path(str(event["manifest"])).parent).photos[0].existing_keywords == [] for event in ready)


def test_queue_runtime_marks_persisted_analysis_failure_and_continues(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    analysis_calls = 0

    class FailThenSucceedVision(_Vision):
        def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
            nonlocal analysis_calls
            analysis_calls += 1
            if analysis_calls == 1:
                raise ValueError("local model failure")
            return super().analyze(model, image, **kwargs)

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=FailThenSucceedVision,
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []

    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)

    failed, ready = runtime.current_items(session_id)
    assert failed.state == "failed"
    assert failed.source_run_id is not None
    assert ready.state == "ready"
    failed_event = next(
        event
        for event in reversed(events)
        if event.get("type") == "queue_item"
        and event.get("item_id") == failed.item_id
        and event.get("state") == "failed"
    )
    manifest_path = Path(str(failed_event["manifest"]))
    assert load_manifest(manifest_path.parent).photos[0].scan_state == "analysis_failed"
    assert any(
        event.get("type") == "queue_session"
        and event.get("attention") == 1
        and event.get("ready") == 1
        for event in events
    )


def test_queue_runtime_keeps_low_confidence_noop_ready(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])

    class LowConfidenceVision(_Vision):
        def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
            del model, image, kwargs
            return VisionResult(
                keywords=("gato",),
                caption="Un gato.",
                contains_people=False,
                contains_text=False,
                confidence=0.1,
            )

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=LowConfidenceVision,
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []

    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)

    assert [item.state for item in runtime.current_items(session_id)] == ["ready", "ready"]
    manifests = [
        load_manifest(Path(str(event["manifest"])).parent)
        for event in events
        if event.get("type") == "queue_item"
        and event.get("state") == "ready"
        and event.get("manifest") is not None
    ]
    assert len(manifests) == 2
    assert all(manifest.photos[0].scan_state == "noop" for manifest in manifests)
    assert all(
        {error["code"] for error in manifest.photos[0].errors} == {"LOW_CONFIDENCE"}
        for manifest in manifests
    )


def test_queue_runtime_surfaces_photoscript_permission_instead_of_skipping_all_photos(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])

    class PermissionDeniedBridge(_Bridge):
        def read(self, local_id: str) -> ScriptPhotoRecord:
            del local_id
            raise PhotoScriptPermissionError()

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=lambda: PermissionDeniedBridge({}),
    ))
    session_id = str(uuid.uuid4())

    try:
        runtime.handle(IPCRequest("start", "queue_start", {
            "session_id": session_id,
            "revision": 0,
            "decision_id": str(uuid.uuid4()),
            "runs_root": str(runs_root),
            "settings_path": str(settings),
        }), lambda event: None)
    except PhotoScriptPermissionError as error:
        assert error.code == "PHOTOS_AUTOMATION_DENIED"
    else:
        raise AssertionError("PhotoScript permission denial was silently ignored")


def test_queue_runtime_rejects_helper_photo_access_before_creating_a_session(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])

    class DeniedSelector(_Selector):
        def authorization_status(self) -> str:
            return "denied"

        def select(self, *, limit: int, randomize: bool = False) -> PhotoSelection:
            del limit, randomize
            raise AssertionError("selection must not start when helper PhotoKit access is denied")

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        selector_factory=lambda: DeniedSelector([]),
    ))
    session_id = str(uuid.uuid4())

    try:
        runtime.handle(IPCRequest("start", "queue_start", {
            "session_id": session_id,
            "revision": 0,
            "decision_id": str(uuid.uuid4()),
            "runs_root": str(runs_root),
            "settings_path": str(settings),
        }), lambda event: None)
    except Exception as error:
        assert type(error).__name__ == "PhotosAccessError"
    else:
        raise AssertionError("helper PhotoKit denial was not surfaced")
    assert session_id not in runtime._active


def test_queue_runtime_creates_the_private_runs_root_after_photo_access_succeeds(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    runs_root.rmdir()
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []

    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)

    ready = [event for event in events if event.get("type") == "queue_item" and event.get("state") == "ready"]
    assert runs_root.is_dir()
    assert len(ready) == 2


def test_queue_persist_loads_private_edits_and_is_idempotent(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)
    item = runtime.current_items(session_id)[0]
    decision_id = str(uuid.uuid4())
    decision_root = runs_root.parent / "queue-sessions"
    _write_decision(
        decision_root,
        session_id=session_id,
        item_id=item.item_id,
        revision=item.revision,
        decision_id=decision_id,
    )
    request = IPCRequest("persist", "queue_persist", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": decision_id,
    })

    runtime.handle(request, events.append)
    runtime.handle(request, events.append)

    assert len(apply_calls) == 1
    assert runtime.item(session_id, item.item_id, item.revision).state == "verified"
    reviewed = load_manifest(apply_calls[0].parent)
    assert reviewed.schema_version == 4
    assert reviewed.photos[0].approved_keywords == ["gato", "perro", "mascotas"]
    assert reviewed.photos[0].approved_caption == "Un gato y un perro descansan juntos."


def test_queue_persist_rejects_invalid_decision_before_admitting_write(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    item = runtime.current_items(session_id)[0]
    decision_id = str(uuid.uuid4())
    decision_path = (
        runs_root.parent / "queue-sessions" / session_id / "decisions" / f"{decision_id}.json"
    )
    decision_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    decision_path.write_text("{}", encoding="utf-8")
    decision_path.chmod(0o600)

    with pytest.raises(QueueDecisionInvalidError):
        runtime.handle(IPCRequest("persist", "queue_persist", {
            "session_id": session_id,
            "item_id": item.item_id,
            "revision": item.revision,
            "decision_id": decision_id,
        }), lambda event: None)

    assert apply_calls == []
    assert runtime.item(session_id, item.item_id, item.revision).state == "ready"
    assert load_session(runs_root.parent / "queue-sessions" / session_id).decisions == []


def test_queue_persist_preserves_distinct_captions_for_two_queued_decisions(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    first_apply_started = threading.Event()
    release_first_apply = threading.Event()
    reviewed_captions: list[str | None] = []

    def apply_runner(path: Path) -> WorkflowResult:
        apply_calls.append(path)
        manifest = load_manifest(path.parent)
        reviewed_captions.append(manifest.photos[0].approved_caption)
        if len(reviewed_captions) == 1:
            first_apply_started.set()
            assert release_first_apply.wait(timeout=5)
        for photo in manifest.photos:
            photo.apply_state = "verified"
            photo.applied_keywords = list(photo.proposed_keywords)
            if photo.proposed_caption is not None:
                photo.applied_caption = photo.proposed_caption
                photo.caption_state = "verified"
            photo.mutation_digest = compute_mutation_digest(manifest, photo)
        write_manifest(path.parent, manifest)
        return WorkflowResult(exit_code=0, manifest_path=path, manifest=manifest)

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        apply_runner=apply_runner,
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)
    first, second = runtime.current_items(session_id)[:2]
    decision_root = runs_root.parent / "queue-sessions"
    first_decision = str(uuid.uuid4())
    second_decision = str(uuid.uuid4())
    _write_decision(
        decision_root,
        session_id=session_id,
        item_id=first.item_id,
        revision=first.revision,
        decision_id=first_decision,
        caption="Un gato descansa en el sofa.",
    )
    _write_decision(
        decision_root,
        session_id=session_id,
        item_id=second.item_id,
        revision=second.revision,
        decision_id=second_decision,
        caption="Un perro mira por la ventana.",
    )
    errors: list[BaseException] = []

    def submit(item: QueueItem, decision_id: str) -> None:
        try:
            runtime.handle(IPCRequest(decision_id, "queue_persist", {
                "session_id": session_id,
                "item_id": item.item_id,
                "revision": item.revision,
                "decision_id": decision_id,
            }), events.append)
        except BaseException as error:
            errors.append(error)

    first_thread = threading.Thread(target=submit, args=(first, first_decision))
    second_thread = threading.Thread(target=submit, args=(second, second_decision))
    first_thread.start()
    assert first_apply_started.wait(timeout=5)
    second_thread.start()
    deadline = time.monotonic() + 5
    while runtime.item(session_id, second.item_id, second.revision).state != "save_queued" and time.monotonic() < deadline:
        time.sleep(0.001)

    assert runtime.item(session_id, first.item_id, first.revision).state == "saving"
    assert runtime.item(session_id, second.item_id, second.revision).state == "save_queued"

    release_first_apply.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert errors == []
    assert reviewed_captions == [
        "Un gato descansa en el sofa.",
        "Un perro mira por la ventana.",
    ]
    assert [runtime.item(session_id, item.item_id, item.revision).state for item in (first, second)] == [
        "verified",
        "verified",
    ]
    assert any(
        event.get("type") == "queue_session"
        and event.get("save_queued") == 1
        and event.get("saving") == 1
        for event in events
    )


def test_queue_persist_keeps_item_uncertain_when_approved_caption_is_not_finalized(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)

    def apply_runner(path: Path) -> WorkflowResult:
        apply_calls.append(path)
        manifest = load_manifest(path.parent)
        for photo in manifest.photos:
            photo.apply_state = "noop"
            photo.caption_state = "proposed"
        write_manifest(path.parent, manifest)
        return WorkflowResult(exit_code=0, manifest_path=path, manifest=manifest)

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        apply_runner=apply_runner,
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)
    item = runtime.current_items(session_id)[0]
    decision_root = runs_root.parent / "queue-sessions"
    decision_id = str(uuid.uuid4())
    _write_decision(
        decision_root,
        session_id=session_id,
        item_id=item.item_id,
        revision=item.revision,
        decision_id=decision_id,
        caption="Un caption aprobado pendiente de verificación.",
    )

    runtime.handle(IPCRequest(decision_id, "queue_persist", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": decision_id,
    }), events.append)

    assert apply_calls
    assert runtime.item(session_id, item.item_id, item.revision).state == "uncertain"
    reviewed = load_manifest(apply_calls[0].parent)
    assert reviewed.photos[0].approved_caption == "Un caption aprobado pendiente de verificación."
    assert reviewed.photos[0].caption_state == "proposed"
    assert any(
        event.get("type") == "queue_item"
        and event.get("item_id") == item.item_id
        and event.get("state") == "uncertain"
        for event in events
    )


def test_queue_resume_after_helper_restart_restores_ready_manifest(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    events: list[dict[str, object]] = []
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), events.append)

    ready = restarted.current_items(session_id)
    assert [item.state for item in ready] == ["ready", "ready"]
    assert any(event.get("type") == "queue_item" and event.get("state") == "ready" for event in events)

    item = ready[0]
    decision_id = str(uuid.uuid4())
    _write_decision(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=item.item_id,
        revision=item.revision,
        decision_id=decision_id,
    )
    restarted.handle(IPCRequest("persist", "queue_persist", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": decision_id,
    }), events.append)

    assert len(apply_calls) == 1
    assert restarted.item(session_id, item.item_id, item.revision).state == "verified"


def test_queue_resume_publishes_ready_items_without_rereading_photos(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)

    read_calls: list[str] = []

    class FailOnReadBridge(_Bridge):
        def read(self, local_id: str) -> ScriptPhotoRecord:
            read_calls.append(local_id)
            raise AssertionError(f"ready item was reread: {local_id}")

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=lambda: FailOnReadBridge({}),
        runs_root_factory=lambda: runs_root,
    ))
    events: list[dict[str, object]] = []
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), events.append)

    ready = restarted.current_items(session_id)
    assert [item.state for item in ready] == ["ready", "ready"]
    assert sum(
        event.get("type") == "queue_item" and event.get("state") == "ready"
        for event in events
    ) == 2
    assert read_calls == []


def test_queue_resume_publishes_persisted_cards_before_photos_reconnects(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    persisted = load_session(runs_root.parent / "queue-sessions" / session_id)
    entered = threading.Event()

    def blocked_bridge() -> _Bridge:
        entered.set()
        raise AssertionError("recovery should not reconnect to Photos for persisted cards")

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=blocked_bridge,
        runs_root_factory=lambda: runs_root,
    ))
    events: list[dict[str, object]] = []
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), events.append)
    assert not entered.is_set()
    assert sum(event.get("type") == "queue_item" and event.get("state") == "ready" for event in events) == 2
    assert restarted.current_items(session_id)


def test_queue_resume_reclassifies_legacy_ready_analysis_failure_for_retry(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    _write_settings(settings, limit=1, concurrency=1)

    class FailingVision(_Vision):
        def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
            del model, image, kwargs
            raise ValueError("local model failure")

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=FailingVision,
    ))
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    failed = persisted.items[0]
    assert failed.state == "failed"
    persisted.checkpoint_item(
        item_id=failed.item_id,
        revision=failed.revision,
        state="ready",
    )
    write_session(session_dir, persisted)

    read_calls: list[str] = []
    original_factory = runtime.dependencies.bridge_factory

    def recording_bridge() -> _Bridge:
        bridge = original_factory()
        original_read = bridge.read

        def read(local_id: str) -> ScriptPhotoRecord:
            read_calls.append(local_id)
            return original_read(local_id)

        bridge.read = read  # type: ignore[method-assign]
        return bridge

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=recording_bridge,
        vision_factory=_Vision,
        runs_root_factory=lambda: runs_root,
    ))
    events: list[dict[str, object]] = []
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), events.append)

    recovered = restarted.item(session_id, failed.item_id, failed.revision)
    assert recovered.state == "failed"
    assert recovered.source_run_id == failed.source_run_id
    failed_event = next(
        event
        for event in reversed(events)
        if event.get("type") == "queue_item"
        and event.get("item_id") == failed.item_id
        and event.get("state") == "failed"
    )
    assert load_manifest(Path(str(failed_event["manifest"])).parent).photos[0].scan_state == "analysis_failed"
    assert read_calls == []

    decision_id = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=failed.item_id,
        revision=failed.revision,
        decision_id=decision_id,
    )
    restarted.handle(IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": failed.item_id,
        "revision": failed.revision,
        "decision_id": decision_id,
    }), events.append)
    restarted.wait(session_id)

    retry = restarted.item(session_id, failed.item_id, failed.revision + 1)
    assert retry.state == "ready"
    assert retry.source_run_id != failed.source_run_id
    assert len(read_calls) == 1


def test_queue_resume_never_retries_an_interrupted_write(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    item = next(item for item in persisted.items if item.state == "ready")
    persisted.record_decision(
        decision_id=str(uuid.uuid4()),
        item_id=item.item_id,
        revision=item.revision,
        action="persist",
    )
    write_session(session_dir, persisted)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)

    assert restarted.item(session_id, item.item_id, item.revision).state == "uncertain"
    assert apply_calls == []


def test_queue_resume_never_retries_a_waiting_write(tmp_path: Path) -> None:
    apply_calls: list[Path] = []
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=apply_calls)
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    item = next(item for item in persisted.items if item.state == "ready")
    persisted.record_decision(
        decision_id=str(uuid.uuid4()),
        item_id=item.item_id,
        revision=item.revision,
        action="persist",
    )
    assert persisted.current_item(item.item_id).state == "save_queued"
    write_session(session_dir, persisted)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)

    assert restarted.item(session_id, item.item_id, item.revision).state == "uncertain"
    assert apply_calls == []


def test_queue_resume_rehydrates_uncertain_item_manifest_for_review(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    item = next(item for item in persisted.items if item.state == "ready")
    persisted.checkpoint_item(
        item_id=item.item_id,
        revision=item.revision,
        state="uncertain",
    )
    write_session(session_dir, persisted)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    events: list[dict[str, object]] = []
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), events.append)

    recovered = [
        event for event in events
        if event.get("type") == "queue_item"
        and event.get("item_id") == item.item_id
        and event.get("state") == "uncertain"
    ]
    assert recovered and recovered[-1].get("manifest")


def test_queue_resume_restores_failed_item_for_safe_rescan(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    item = next(item for item in persisted.items if item.state == "ready")
    source_manifest = next(
        load_manifest(path.parent)
        for path in runs_root.glob("*/manifest.json")
        if load_manifest(path.parent).run_id == item.source_run_id
    )
    local_id = source_manifest.photos[0].photos_local_identifier
    persisted.checkpoint_item(
        item_id=item.item_id,
        revision=item.revision,
        state="failed",
    )
    write_session(session_dir, persisted)

    read_calls: list[str] = []
    original_factory = runtime.dependencies.bridge_factory

    def recording_bridge() -> _Bridge:
        bridge = original_factory()
        original_read = bridge.read

        def read(candidate_local_id: str) -> ScriptPhotoRecord:
            read_calls.append(candidate_local_id)
            return original_read(candidate_local_id)

        bridge.read = read  # type: ignore[method-assign]
        return bridge

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=recording_bridge,
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    assert read_calls == []

    decision_id = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=item.item_id,
        revision=item.revision,
        decision_id=decision_id,
    )
    restarted.handle(IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": decision_id,
    }), lambda event: None)
    restarted.wait(session_id)

    latest = restarted.item(session_id, item.item_id, item.revision + 1)
    assert latest.state == "ready"
    assert read_calls == [local_id]


def test_queue_resume_restores_failed_rescan_from_the_previous_attempt(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    session_dir = runs_root.parent / "queue-sessions" / session_id
    persisted = load_session(session_dir)
    first_attempt = next(item for item in persisted.items if item.state == "ready")
    persisted.record_decision(
        decision_id=str(uuid.uuid4()),
        item_id=first_attempt.item_id,
        revision=first_attempt.revision,
        action="rescan",
    )
    failed_attempt = persisted.current_item(first_attempt.item_id)
    persisted.checkpoint_item(
        item_id=failed_attempt.item_id,
        revision=failed_attempt.revision,
        state="failed",
    )
    write_session(session_dir, persisted)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    decision_id = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=failed_attempt.item_id,
        revision=failed_attempt.revision,
        decision_id=decision_id,
    )

    restarted.handle(IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": failed_attempt.item_id,
        "revision": failed_attempt.revision,
        "decision_id": decision_id,
    }), lambda event: None)
    restarted.wait(session_id)

    latest = restarted.item(
        session_id,
        failed_attempt.item_id,
        failed_attempt.revision + 1,
    )
    assert latest.state == "ready"
    assert latest.source_run_id is not None


def test_recovered_queue_is_registered_before_replacing_interrupted_candidate(tmp_path: Path) -> None:
    runtime, runs_root, _ = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    session = QueueSession.new(session_id=session_id, config=SessionConfig(
        photo_count=1, inference_concurrency=1, model_policy="single", model="qwen3-vl:4b",
    ))
    session.enqueue(item_id=str(uuid.uuid4()), state="analyzing")
    write_session(runs_root.parent / "queue-sessions" / session_id, session)
    entered, release = threading.Event(), threading.Event()
    original_factory = runtime.dependencies.bridge_factory

    def blocked_bridge():
        entered.set()
        assert release.wait(5)
        return original_factory()

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies, runs_root_factory=lambda: runs_root, bridge_factory=blocked_bridge,
    ))
    failures = []

    def resume():
        try:
            restarted.handle(IPCRequest("resume", "queue_resume", {
                "session_id": session_id, "revision": session.revision,
                "decision_id": str(uuid.uuid4()),
            }), lambda event: None)
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=resume)
    thread.start()
    paused = threading.Event()

    def pause():
        try:
            active = restarted._require_active(session_id)
            restarted.handle(IPCRequest("pause", "queue_pause", {
                "session_id": session_id, "revision": active.coordinator.session.revision,
                "decision_id": str(uuid.uuid4()),
            }), lambda event: None)
            paused.set()
        except Exception as error:
            failures.append(error)

    pause_thread = threading.Thread(target=pause)
    try:
        assert entered.wait(5)
        assert restarted._require_active(session_id).coordinator.session.session_id == session_id
        pause_thread.start()
        assert paused.wait(1), "recovered queue controls waited for Photos"
    finally:
        release.set()
        thread.join(5)
        if pause_thread.ident is not None:
            pause_thread.join(5)
    assert not failures
    restarted.wait(session_id)


def test_queue_resume_requeues_read_only_work_and_cleans_stale_exports(tmp_path: Path) -> None:
    runtime, runs_root, _ = _runtime(tmp_path, apply_calls=[])
    session_id = str(uuid.uuid4())
    session = QueueSession.new(
        session_id=session_id,
        config=SessionConfig(
            photo_count=1,
            inference_concurrency=1,
            model_policy="single",
            model="qwen3-vl:4b",
        ),
    )
    interrupted = session.enqueue(item_id=str(uuid.uuid4()), state="analyzing")
    session_dir = runs_root.parent / "queue-sessions" / session_id
    write_session(session_dir, session)

    run_id = str(uuid.uuid4())
    stale_run = runs_root / f"20260831T120000Z-{run_id[:8]}"
    stale_run.mkdir(mode=0o700)
    stale_workspace = TemporaryExportWorkspace(stale_run, run_id)
    stale_workspace.__enter__()
    stale_lock = stale_workspace.root / TemporaryExportWorkspace.lock_name
    stale_workspace._close_lock()
    stale_lock.unlink()
    stale_lock.write_bytes(b"")
    os.chmod(stale_lock, 0o600)

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": session.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    restarted.wait(session_id)

    assert restarted.item(session_id, interrupted.item_id, interrupted.revision).state == "ready"
    assert not stale_workspace.root.exists()


def test_queue_resume_preserves_no_repeat_for_a_discarded_photo(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir(mode=0o700)
    settings = tmp_path / "settings.json"
    _write_settings(settings, limit=1, concurrency=1)
    selected = SelectedPhoto("only-photo", datetime(2026, 1, 1))
    record = ScriptPhotoRecord(
        uuid=str(uuid.uuid4()),
        local_id=selected.local_id,
        title="",
        date=selected.creation_date,
        existing_keywords=(),
        description="",
    )
    dependencies = QueueRuntimeDependencies(
        selector_factory=lambda: _Selector([selected]),
        bridge_factory=lambda: _Bridge({selected.local_id: record}),
        vision_factory=_Vision,
        landmark_resolver_factory=_Landmarks,
        runs_root_factory=lambda: runs_root,
    )
    runtime = ContinuousQueueRuntime(dependencies=dependencies)
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    item = runtime.current_items(session_id)[0]
    runtime.handle(IPCRequest("discard", "queue_discard", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    persisted = load_session(runs_root.parent / "queue-sessions" / session_id)

    restarted = ContinuousQueueRuntime(dependencies=dependencies)
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    restarted.wait(session_id)

    assert [(candidate.item_id, candidate.state) for candidate in restarted.current_items(session_id)] == [
        (item.item_id, "discarded"),
    ]


def test_queue_rescan_loads_private_options_and_preserves_the_previous_attempt(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    calls: list[tuple[str, dict[str, object]]] = []
    checked: list[str] = []
    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=lambda: _RecordingVision(calls, checked),
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)
    original = runtime.current_items(session_id)[0]
    decision_id = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=original.item_id,
        revision=original.revision,
        decision_id=decision_id,
    )
    request = IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": original.item_id,
        "revision": original.revision,
        "decision_id": decision_id,
    })

    runtime.handle(request, events.append)
    runtime.handle(request, events.append)
    runtime.wait(session_id)

    persisted = load_session(runs_root.parent / "queue-sessions" / session_id)
    attempts = [item for item in persisted.items if item.item_id == original.item_id]
    assert [item.revision for item in attempts] == [1, 2]
    assert all(item.source_run_id is not None for item in attempts)
    assert attempts[0].source_run_id != attempts[1].source_run_id
    assert checked.count("qwen3-vl:8b") == 1
    model, kwargs = calls[-1]
    assert model == "qwen3-vl:8b"
    assert kwargs == {
        "analysis_profile": "free_local",
        "analysis_layers": {
            "places": False,
            "documents_text": True,
            "people_accessories": True,
            "semantic_normalization": True,
        },
        "additional_information": "Hay dos especies distintas visibles.",
        "analysis_prompt": "Distingue cada animal por sus rasgos visibles.",
    }


def test_queue_rescan_persists_a_safe_read_failure_from_the_identity_check(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    fail_rescan_read = False
    original_factory = runtime.dependencies.bridge_factory

    def bridge_factory() -> _Bridge:
        bridge = original_factory()
        original_read = bridge.read

        def read(local_id: str) -> ScriptPhotoRecord:
            if fail_rescan_read:
                raise OSError("private PhotoScript failure")
            return original_read(local_id)

        bridge.read = read  # type: ignore[method-assign]
        return bridge

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=bridge_factory,
    ))
    session_id = str(uuid.uuid4())
    events: list[dict[str, object]] = []
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), events.append)
    runtime.wait(session_id)
    original = runtime.current_items(session_id)[0]
    fail_rescan_read = True
    decision_id = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=original.item_id,
        revision=original.revision,
        decision_id=decision_id,
    )

    runtime.handle(IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": original.item_id,
        "revision": original.revision,
        "decision_id": decision_id,
    }), events.append)
    runtime.wait(session_id)

    failed = runtime.item(session_id, original.item_id, original.revision + 1)
    assert failed.state == "failed"
    assert failed.source_run_id is not None
    manifest_path = next(
        Path(str(event["manifest"]))
        for event in events
        if event.get("type") == "queue_item"
        and event.get("item_id") == original.item_id
        and event.get("revision") == failed.revision
        and event.get("state") == "failed"
    )
    manifest = load_manifest(manifest_path.parent)
    assert manifest.photos[0].errors == [{"stage": "metadata", "code": "READ_FAILED"}]


def test_queue_rescan_refreshes_location_for_restored_item_when_maps_enabled(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    base_factory = runtime.dependencies.bridge_factory

    def located_bridge() -> _Bridge:
        bridge = base_factory()
        original_read = bridge.read

        def read(local_id: str) -> ScriptPhotoRecord:
            return replace(original_read(local_id), location=(19.4326, -99.1332))

        bridge.read = read  # type: ignore[method-assign]
        return bridge

    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=located_bridge,
    ))
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    persisted = load_session(runs_root.parent / "queue-sessions" / session_id)
    item = next(item for item in persisted.items if item.state == "ready")

    restarted_calls: list[str] = []
    places_calls: list[object] = []
    def recording_bridge() -> _Bridge:
        bridge = located_bridge()
        original_read = bridge.read

        def read(local_id: str) -> ScriptPhotoRecord:
            restarted_calls.append(local_id)
            return original_read(local_id)

        bridge.read = read  # type: ignore[method-assign]
        return bridge

    restarted = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        bridge_factory=recording_bridge,
        places_factory=lambda: _RecordingPlaces(places_calls),
        runs_root_factory=lambda: runs_root,
    ))
    restarted.handle(IPCRequest("resume", "queue_resume", {
        "session_id": session_id,
        "revision": persisted.revision,
        "decision_id": str(uuid.uuid4()),
    }), lambda event: None)
    restarted_revision = load_session(runs_root.parent / "queue-sessions" / session_id).revision
    _write_settings(settings, apple_maps=True)
    restarted.handle(IPCRequest("update", "queue_update", {
        "session_id": session_id,
        "revision": restarted_revision,
        "decision_id": str(uuid.uuid4()),
        "settings_path": str(settings),
    }), lambda event: None)
    rescan_decision = str(uuid.uuid4())
    _write_rescan(
        runs_root.parent / "queue-sessions",
        session_id=session_id,
        item_id=item.item_id,
        revision=item.revision,
        decision_id=rescan_decision,
        places=True,
    )
    restarted.handle(IPCRequest("rescan", "queue_rescan", {
        "session_id": session_id,
        "item_id": item.item_id,
        "revision": item.revision,
        "decision_id": rescan_decision,
    }), lambda event: None)
    restarted.wait(session_id)

    assert restarted_calls == ["local-clean-1"]
    assert places_calls == [(19.4326, -99.1332)]


def test_queue_update_preflights_and_applies_new_settings_to_upcoming_photos(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    _write_settings(settings, limit=1, model="qwen3-vl:4b", include_caption=True)
    calls: list[tuple[str, dict[str, object]]] = []
    checked: list[str] = []
    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=lambda: _RecordingVision(calls, checked),
    ))
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)
    revision = load_session(runs_root.parent / "queue-sessions" / session_id).revision
    _write_settings(settings, limit=2, model="qwen3-vl:8b", include_caption=False)

    runtime.handle(IPCRequest("update", "queue_update", {
        "session_id": session_id,
        "revision": revision,
        "decision_id": str(uuid.uuid4()),
        "settings_path": str(settings),
    }), lambda event: None)
    runtime.wait(session_id)

    assert "qwen3-vl:8b" in checked
    assert [model for model, _ in calls] == ["qwen3-vl:4b", "qwen3-vl:8b"]
    current = runtime.current_items(session_id)
    assert len(current) == 2
    assert load_session(runs_root.parent / "queue-sessions" / session_id).config.model == "qwen3-vl:8b"


def test_queue_update_reconfigures_discovered_photos_that_have_not_started(tmp_path: Path) -> None:
    runtime, runs_root, settings = _runtime(tmp_path, apply_calls=[])
    _write_settings(settings, limit=2, concurrency=1, model="qwen3-vl:4b")
    calls: list[tuple[str, dict[str, object]]] = []
    checked: list[str] = []
    started = threading.Event()
    release = threading.Event()
    runtime = ContinuousQueueRuntime(dependencies=replace(
        runtime.dependencies,
        vision_factory=lambda: _BlockingRecordingVision(calls, checked, started, release),
    ))
    session_id = str(uuid.uuid4())
    runtime.handle(IPCRequest("start", "queue_start", {
        "session_id": session_id,
        "revision": 0,
        "decision_id": str(uuid.uuid4()),
        "runs_root": str(runs_root),
        "settings_path": str(settings),
    }), lambda event: None)
    assert started.wait(timeout=5)
    revision = load_session(runs_root.parent / "queue-sessions" / session_id).revision
    _write_settings(settings, limit=2, concurrency=1, model="qwen3-vl:8b", include_caption=False)

    runtime.handle(IPCRequest("update", "queue_update", {
        "session_id": session_id,
        "revision": revision,
        "decision_id": str(uuid.uuid4()),
        "settings_path": str(settings),
    }), lambda event: None)
    release.set()
    runtime.wait(session_id)

    assert [model for model, _ in calls] == ["qwen3-vl:4b", "qwen3-vl:8b"]
