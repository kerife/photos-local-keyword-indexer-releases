"""Bounded production dispatcher for explicit, durable autonomous campaigns."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, Event, RLock, Thread
from typing import Any, Callable

from .adapters import LocalExportStorageError, PhotoSelection, PhotosAccessError, PhotoScriptPermissionError
from .autonomous_runtime import (
    AutomaticApplyGate, CampaignStore, CampaignStorageError, CampaignValidationError,
    _digest, _identifier, _keys, _read, _write,
)
from .autonomous_inventory import InventoryError
from .ipc import IPCRequest
from .manifest import ManifestError
from .queue_runtime import QueueRuntimeDependencies, QueueRuntimeError, _ensure_private_root, _load_config
from .workflows import (
    _analyze_prepared_photo, _discard_failed_run, _persist_scan,
    _prepare_photo_analysis, _safe_photo_metadata, resolve_model_plan,
)


class AutonomousActiveError(ValueError):
    """The helper is already reserved for a different activation."""


class _PhotoAnalysisFailed(ValueError):
    def __init__(self, codes: set[str]) -> None:
        self.codes = codes


class _ActivationRevoked(ValueError):
    pass


class _PhotosLane:
    """One Photos operation at a time, with accepted writes ahead of reads."""

    def __init__(self) -> None:
        self.condition = Condition()
        self.active = False
        self.writers = 0

    @contextmanager
    def operation(self, *, write: bool = False):
        with self.condition:
            if write:
                self.writers += 1
            try:
                while self.active or (not write and self.writers):
                    self.condition.wait()
                self.active = True
            finally:
                if write:
                    self.writers -= 1
        try:
            yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()


class _LocalExportBridge:
    def __init__(self, bridge: Any, selector: Any, selected: Any) -> None:
        self.bridge, self.selector, self.selected = bridge, selector, selected
        self.metadata = None

    def read(self, local_id: str):
        if local_id != self.selected.local_id:
            raise ValueError("autonomous photo identity changed")
        self.metadata = _safe_photo_metadata(self.bridge.read(local_id), local_id)
        if self.metadata is None:
            raise ValueError("autonomous photo metadata is invalid")
        return self.metadata

    def export(self, photo_uuid: str, destination: Path) -> Path:
        if self.metadata is None or photo_uuid != self.metadata.uuid:
            raise ValueError("autonomous export identity changed")
        return self.selector.export_local(self.selected.local_id, destination)


class AutonomousCampaignRuntime:
    """Explicit activation only; status/recovery never instantiates native clients.

    The server supplies the manual drain and a revocable generation Event. A
    dispatcher thread owns bounded futures until accepted analysis/write work
    finishes; its completion callback returns manual helper ownership.
    """

    def __init__(self, *, dependencies: QueueRuntimeDependencies | None = None) -> None:
        if dependencies is None:
            from .service import app_runs_root
            dependencies = QueueRuntimeDependencies(runs_root_factory=app_runs_root)
        self.dependencies = dependencies
        self._lock = RLock()
        self._controls = RLock()
        self._store: CampaignStore | None = None
        self._thread: Thread | None = None
        self._generation: Event | None = None
        self._provisional: dict[str, Any] | None = None
        self._store_requires_reconciliation = False
        self._root = Path(dependencies.runs_root_factory()).parent / "autonomous-campaigns"
        self._loaded = False
        self._failure_reason: str | None = None

    def _recover(self) -> None:
        if self._loaded:
            return
        latest = self._root / "latest.json"
        activation = self._root / "activation.json"
        value = None
        if activation.exists() or activation.is_symlink():
            intake = _read(activation)
            self._validate_intake(intake)
            if intake["command"] not in {"autonomy_start", "autonomy_resume"}:
                raise CampaignStorageError("activation record is invalid")
            value = {key: intake["payload"][key] for key in ("campaign_id", "decision_id")}
            intake_path = self._root / "intakes" / f"{value['decision_id']}.json"
            if intake_path.exists() or intake_path.is_symlink():
                if _read(intake_path) != intake:
                    raise CampaignStorageError("activation intake changed")
            else:
                _write(intake_path, intake)
        elif latest.exists() or latest.is_symlink():
            value = _read(latest)
            _keys(value, {"campaign_id", "decision_id"})
        if value is not None:
            campaign_id = _identifier(value["campaign_id"])
            decision_id = _identifier(value["decision_id"])
            intake = _read(self._root / "intakes" / f"{decision_id}.json")
            self._validate_intake(intake)
            if intake["payload"]["campaign_id"] != campaign_id:
                raise CampaignStorageError("campaign intake identity changed")
            path = self._root / campaign_id
            if (path / "campaign.json").exists():
                initial_checkpoint_missing = not ((path / "checkpoint.json").exists() or (path / "checkpoint.json").is_symlink())
                self._store = CampaignStore.load_status(path)
                self._store_requires_reconciliation = True
                if initial_checkpoint_missing:
                    provisional_path = self._root / "preparing.json"
                    if provisional_path.exists() or provisional_path.is_symlink():
                        from .ipc import _safe_service_event
                        state = _read(provisional_path)
                        _safe_service_event(state)
                        if state["campaign_id"] == campaign_id and state["revision"] >= self._store.status()["revision"]:
                            with self._store._lock:
                                self._store._checkpoint_state(revision=state["revision"])
            else:
                provisional_path = self._root / "preparing.json"
                if provisional_path.exists() or provisional_path.is_symlink():
                    from .ipc import _safe_service_event
                    state = _read(provisional_path)
                    _safe_service_event(state)
                    if state["campaign_id"] == campaign_id:
                        self._provisional = dict(state, state="paused", reason="recovered", revision=state["revision"] + 1)
                    else:
                        # The atomic activation may precede publication of
                        # the new campaign's secondary preparation checkpoint.
                        self._provisional = self._empty_status(campaign_id, state="paused", reason="recovered")
                else:
                    self._provisional = self._empty_status(campaign_id, state="paused", reason="recovered")
                self._save_provisional()
        self._loaded = True

    @staticmethod
    def _empty_status(campaign_id: str, *, state: str, reason: str) -> dict[str, Any]:
        return dict(type="autonomy_campaign", campaign_id=campaign_id, state=state, reason=reason,
                    **{key: 0 for key in ("revision", "total", "examined", "analyzed", "saved", "no_change", "attention", "remaining", "in_flight", "invalid_count")})

    @staticmethod
    def _validate_intake(value: dict[str, Any]) -> None:
        from .ipc import _validate_payload
        _keys(value, {"command", "payload", "digest"})
        if value["command"] not in {"autonomy_start", "autonomy_resume", "autonomy_pause"}:
            raise CampaignStorageError("campaign intake command is invalid")
        _validate_payload(value["command"], value["payload"])
        if value["digest"] != _digest({"command": value["command"], "payload": value["payload"]}):
            raise CampaignStorageError("campaign intake changed")

    def _intake(self, request: IPCRequest) -> bool:
        _ensure_private_root(self._root)
        _ensure_private_root(self._root / "intakes")
        path = self._root / "intakes" / f"{request.payload['decision_id']}.json"
        control = {"command": request.command, "payload": request.payload}
        value = {**control, "digest": _digest(control)}
        if path.exists() or path.is_symlink():
            existing = _read(path)
            self._validate_intake(existing)
            if existing != value:
                raise CampaignValidationError("campaign decision was reused")
            return False
        if request.command in {"autonomy_start", "autonomy_resume"}:
            # This is the single durable acceptance/recovery publication.
            # The immutable replay index and preparing/latest checkpoints may
            # be interrupted afterward without losing the accepted campaign.
            activation = self._root / "activation.json"
            if activation.exists() or activation.is_symlink():
                previous = _read(activation)
                self._validate_intake(previous)
                previous_path = self._root / "intakes" / f"{previous['payload']['decision_id']}.json"
                if not previous_path.exists() and not previous_path.is_symlink():
                    _write(previous_path, previous)
                if previous["payload"]["decision_id"] == request.payload["decision_id"]:
                    if previous != value:
                        raise CampaignValidationError("campaign decision was reused")
                    return False
            _write(activation, value, replace=True)
        _write(path, value)
        return True

    def _save_provisional(self) -> None:
        if self._provisional is not None:
            _write(self._root / "preparing.json", self._provisional, replace=True)

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            self._recover()
            return self._store.status() if self._store else (dict(self._provisional) if self._provisional else None)

    def revoke(self) -> None:
        """Immediate admission closure, including while manual work drains."""
        with self._lock:
            if self._generation:
                self._generation.set()
            if self._store is not None:
                with self._store._lock:
                    self._store._authorization = None

    def admit_pause(self, request: IPCRequest, *, pending_campaign_id: str | None = None,
                    pending_generation: Event | None = None) -> bool:
        """Validate and durably admit a fresh pause before closing admission.

        The server calls this under its admission lock. It deliberately does
        not take the long control/drain lock, so a new pause revokes preparation
        immediately while replay and conflicting IDs leave a newer run alone.
        """
        with self._lock:
            self._recover()
            current = self.status()
            campaign_id = request.payload["campaign_id"]
            if pending_campaign_id != campaign_id and (current is None or current["campaign_id"] != campaign_id):
                raise CampaignValidationError("campaign is not current")
            try:
                fresh = self._intake(request)
            except CampaignValidationError:
                # An ID conflict is an invalid control, not a disk failure.
                raise
            except (CampaignStorageError, QueueRuntimeError, OSError):
                if pending_generation is not None:
                    pending_generation.set()
                self._failed("storage", lambda event: None)
                raise
            if fresh:
                self.revoke()
            return fresh

    def handle(self, request: IPCRequest, emit: Callable[[dict[str, Any]], None], *,
               generation: Event | None = None, drain: Callable[[], None] = lambda: None,
               released: Callable[[], None] = lambda: None, pause_admitted: bool | None = None) -> bool:
        """Return True when a background dispatcher owns the release callback."""
        if request.command == "autonomy_status":
            state = self.status()
            if state:
                emit(state)
            return False
        if request.command == "autonomy_pause":
            if pause_admitted is None:
                pause_admitted = self.admit_pause(request)
            if not pause_admitted:
                state = self.status()
                if state:
                    emit(state)
                return False
        with self._controls:
            with self._lock:
                self._recover()
                current = self.status()
                campaign_id = str(request.payload["campaign_id"])
                if request.command != "autonomy_start" and (current is None or current["campaign_id"] != campaign_id):
                    raise CampaignValidationError("campaign is not current")
                if request.command == "autonomy_resume" and self._store_requires_reconciliation:
                    self._store = CampaignStore.load(self._store.path)
                    self._store_requires_reconciliation = False
                    current = self._store.status()
                intake_path = self._root / "intakes" / f"{request.payload['decision_id']}.json"
                if request.command in {"autonomy_start", "autonomy_resume"} and self._thread is not None and self._thread.is_alive() and not intake_path.exists():
                    raise AutonomousActiveError("campaign is active")
                try:
                    fresh = True if pause_admitted else self._intake(request)
                except CampaignValidationError:
                    raise
                except (CampaignStorageError, QueueRuntimeError, OSError):
                    self._failed("storage", emit)
                    raise
                if not fresh:
                    if current:
                        emit(current)
                    return False
                if request.command == "autonomy_pause":
                    if self._store:
                        self._store.pause(request.command, request.payload)
                    elif self._provisional:
                        self._provisional.update(state="paused", reason="user_pause", revision=self._provisional["revision"] + 1)
                        self._save_provisional()
                    thread = self._thread
                else:
                    if self._thread is not None and self._thread.is_alive():
                        raise AutonomousActiveError("campaign is active")
                    generation = generation or Event()
                    self._generation = generation
                    thread = None
            if request.command == "autonomy_pause":
                if thread:
                    thread.join()
                state = self.status()
                if state:
                    emit(state)
                return False
            assert generation is not None
            if request.command == "autonomy_start":
                runs_root = Path(str(request.payload["runs_root"]))
                if runs_root != self._root.parent / "runs":
                    raise CampaignValidationError("campaign root is invalid")
                if (self._root / campaign_id).exists():
                    raise CampaignValidationError("campaign identity already exists")
                with self._lock:
                    self._store = None
                    self._store_requires_reconciliation = False
                    self._provisional = self._empty_status(campaign_id, state="preparing", reason="manual_drain")
                    self._save_provisional()
                    _write(self._root / "latest.json", {"campaign_id": campaign_id, "decision_id": request.payload["decision_id"]}, replace=True)
                try:
                    config = _load_config(Path(str(request.payload["settings_path"])), runs_root=runs_root)
                except Exception:
                    self._failed("storage", emit)
                    raise
            else:
                if self._store is None:
                    raise CampaignValidationError("interrupted inventory requires a new campaign")
                config = self._store.config
                runs_root = self._root.parent / "runs"
                with self._store._lock:
                    self._store._checkpoint_state(state="preparing", reason="manual_drain")
            emit(self.status())
            drain()
            if generation.is_set():
                self._paused_preparation(emit)
                return False
            plan = resolve_model_plan(policy=config.model_policy, model=config.model,
                                      fast_model=config.fast_model, detailed_model=config.detailed_model)
            try:
                vision = self.dependencies.vision_factory()
                versions = {model: str(vision.check_model(model)) for model in plan.required_models}
            except Exception:
                self._paused_preparation(emit, reason="none")
                raise
            if generation.is_set():
                self._paused_preparation(emit)
                return False
            try:
                if request.command == "autonomy_start":
                    with self._lock:
                        self._provisional.update(reason="snapshot", revision=self._provisional["revision"] + 1)
                        self._save_provisional()
                    emit(self.status())
                    selector = self.dependencies.selector_factory()
                    def records():
                        for record in selector.inventory():
                            if generation.is_set():
                                raise _ActivationRevoked()
                            yield record
                    store = CampaignStore.create(self._root / campaign_id, campaign_id=campaign_id,
                                                 config=config, records=records(), limit=request.payload["limit"],
                                                 created_at=self.dependencies.now_utc())
                    with self._lock:
                        self._store = store
                        with store._lock:
                            store._checkpoint_state(revision=self._provisional["revision"] + 1)
                        self._provisional = None
                else:
                    store = self._store
                    selector = self.dependencies.selector_factory()
                assert store is not None
                with self._lock:
                    if generation.is_set():
                        self._paused_preparation(emit)
                        return False
                    store.authorize(request.command, request.payload, now=self.dependencies.now_utc())
                    _ensure_private_root(runs_root)
                    self._failure_reason = None
                    self._thread = Thread(target=self._drive, args=(store, selector, plan, versions, generation, emit, released), daemon=True)
                    self._thread.start()
                emit(store.status())
                return True
            except _ActivationRevoked:
                self._paused_preparation(emit)
                return False
            except (PhotosAccessError, PhotoScriptPermissionError):
                self._failed("permission", emit)
                raise
            except (CampaignStorageError, InventoryError, OSError):
                self._failed("storage", emit)
                raise

    def _paused_preparation(self, emit, *, reason="user_pause") -> None:
        with self._lock:
            if self._store:
                with self._store._lock:
                    self._store._authorization = None
                    self._store._checkpoint_state(state="paused", reason=reason)
            elif self._provisional:
                self._provisional.update(state="paused", reason=reason, revision=self._provisional["revision"] + 1)
                self._save_provisional()
            state = self.status()
        if state:
            emit(state)

    def _failed(self, reason, emit) -> None:
        with self._lock:
            self._failure_reason = reason
            if self._generation:
                self._generation.set()
            if self._store:
                self._store.fail_closed(reason)
            elif self._provisional:
                self._provisional.update(state="paused", reason=reason, revision=self._provisional["revision"] + 1)
                try:
                    self._save_provisional()
                except CampaignStorageError:
                    self._provisional.update(reason="storage")
            state = self.status()
        if state:
            emit(state)

    def _source(self, store, position) -> Path | None:
        artifact = store._position_path(position) / "analysis.json"
        if not artifact.exists() and not artifact.is_symlink():
            return None
        value = _read(artifact)
        _keys(value, {"position", "run_name", "run_id", "source_digest"})
        name = value["run_name"]
        if type(name) is not str or Path(name).name != name or name in {".", ".."}:
            raise CampaignStorageError("analysis location is invalid")
        path = self._root.parent / "runs" / name / "manifest.json"
        store.record_analysis(position, path)
        return path

    def _analyze(self, selected, selector, config, plan, versions, lane) -> Path:
        deps = self.dependencies
        now, run_id = deps.now_utc(), str(deps.uuid4())
        run_dir = self._root.parent / "runs" / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{run_id[:8]}"
        run_dir.mkdir(mode=0o700)
        try:
            with deps.workspace_factory(run_dir, run_id) as workspace:
                with lane.operation():
                    prepared = _prepare_photo_analysis(
                        selected, bridge=_LocalExportBridge(deps.bridge_factory(), selector, selected),
                        workspace=workspace, model_plan=plan,
                        landmark_resolver=deps.landmark_resolver_factory(),
                        places_client=deps.places_factory() if config.apple_maps else None,
                        cancel_requested=lambda: False, ollama_versions=versions)
                if prepared.errors:
                    raise _PhotoAnalysisFailed({code for _, code in prepared.errors})
                photo = _analyze_prepared_photo(prepared, vision=deps.vision_factory(), include_caption=config.include_caption)
                selection = PhotoSelection(photos=(selected,), requested=1, eligible=1,
                                           screenshots_excluded=0, access="authorized", strategy="recent")
                try:
                    _, path = _persist_scan(run_dir, run_id=run_id, created_at=now, model_plan=plan,
                                            ollama_versions=versions, selection=selection, photos=[photo],
                                            scan_status="ready_with_errors" if photo.errors else "ready",
                                            captions_requested=config.include_caption)
                except (ManifestError, OSError) as error:
                    raise CampaignStorageError("analysis evidence could not be persisted") from error
                if prepared.exported is None or photo.scan_state == "analysis_failed":
                    raise _PhotoAnalysisFailed({error["code"] for error in photo.errors})
                return path
        except _PhotoAnalysisFailed:
            # Keep the failed source run as diagnostic evidence, but do not
            # count failed preparation/nonreturned inference as analyzed.
            if not (run_dir / "manifest.json").exists():
                _discard_failed_run(run_dir)
            raise
        except BaseException:
            _discard_failed_run(run_dir)
            raise

    def _drive(self, store, selector, plan, versions, generation, emit, released) -> None:
        lane = _PhotosLane()
        gate = AutomaticApplyGate(store, bridge_factory=self.dependencies.bridge_factory,
                                  review_runner=self.dependencies.review_runner, apply_runner=self.dependencies.apply_runner)
        writes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autonomous-write")
        activity_revisions: dict[int, int] = {}

        def activity(position, selected, state) -> None:
            with self._lock:
                revision = activity_revisions.get(position, -1) + 1
                activity_revisions[position] = revision
            emit({
                "type": "autonomy_activity",
                "campaign_id": store.metadata["campaign_id"],
                "revision": revision,
                "position": position,
                "state": state,
                "photos_local_identifier": selected.local_id,
            })

        def apply(position, selected, source):
            with lane.operation(write=True):
                activity(position, selected, "saving")
                outcome = gate.process(position, source)
                failure = store.failure_reason
                if failure is not None:
                    self._failure_reason = failure
                    generation.set()
                return outcome

        def item(position, selected):
            try:
                activity(position, selected, "analyzing")
                source = self._source(store, position)
                if source is None:
                    source = self._analyze(selected, selector, store.config, plan, versions, lane)
                activity(position, selected, "validating")
                activity(position, selected, "save_queued")
                writes.submit(apply, position, selected, source).result()
            except _PhotoAnalysisFailed as error:
                store.settle(position, "attention", reason="failed")
                if error.codes & {"PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED"}:
                    self._failed("permission", emit)
            except (PhotosAccessError, PhotoScriptPermissionError):
                store.settle(position, "attention", reason="failed")
                self._failed("permission", emit)
            except (CampaignStorageError, InventoryError, LocalExportStorageError, OSError):
                self._failed("storage", emit)
                store.release(position)
            except Exception:
                store.settle(position, "attention", reason="failed")
            finally:
                activity(position, selected, "settled")
                emit(store.status())

        try:
            pending = iter(store.pending())
            futures = set()
            exhausted = False
            with ThreadPoolExecutor(max_workers=store.config.inference_concurrency, thread_name_prefix="autonomous-analysis") as pool:
                while True:
                    while not exhausted and not generation.is_set() and store.status()["state"] == "running" and len(futures) < store.config.photo_count:
                        candidate = next(pending, None)
                        if candidate is None:
                            exhausted = True
                            break
                        position, selected = candidate
                        with self._lock:
                            if generation.is_set() or store.status()["state"] != "running":
                                break
                            store.mark_examined(position)
                            activity(position, selected, "preparing")
                            futures.add(pool.submit(item, position, selected))
                        emit(store.status())
                    if not futures:
                        break
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
            with store._lock:
                if self._failure_reason is not None:
                    store._authorization = None
                    store._checkpoint_state(state="paused", reason=self._failure_reason)
                elif store.status()["remaining"] == 0:
                    store._authorization = None
                    store._checkpoint_state(state="completed", reason="none")
                elif generation.is_set() or store._authorization is None:
                    store._authorization = None
                    reason = store.status()["reason"]
                    store._checkpoint_state(state="paused", reason=reason if reason in {"permission", "storage"} else "user_pause")
            emit(store.status())
        except Exception:
            self._failed("storage", emit)
        finally:
            writes.shutdown(wait=True)
            released()

    def shutdown(self) -> None:
        self.revoke()
        with self._lock:
            thread = self._thread
        if thread:
            thread.join()
